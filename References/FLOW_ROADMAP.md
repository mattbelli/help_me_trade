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

- [x] **0.1 Liveness proof. — DONE 2026-07-27.** `FeedLiveness` in `signals/options_flow/dbn_stream.py`,
      judged on **records, not `is_connected()`** — the failure that matters is a socket that still
      reports healthy while nothing arrives. **Three faults, deliberately separate because they need
      different responses:** subscription never acked → raises (and it is the only check valid outside
      RTH, since symbol mappings arrive on subscribe regardless of market activity); no records for 90s
      during RTH → raises `FeedDead`, inside the 2-minute budget; records arriving but no prints for
      300s → logs loudly and does **not** raise, because a symbology/meta fault should not tear down a
      healthy socket. Prints/min counter added so a dying feed is visible before a threshold fires.
      RTH from the existing holiday/half-day-aware `trading.market_hours.is_market_open`. 15 tests,
      including the subtle one: a closed-market tick must **reset** the silence clocks or the first
      tick after the bell reports a dead feed on a healthy one.
      ⚠️ **Shipped with a defect and corrected the same day by review — read this before trusting it.**
      The first version judged silence on `msgs`, but databento dispatches **gateway heartbeats (30s)**
      to the same callback, so `msgs` advanced through a completely dead feed and the check could never
      fire. Now judged on `data_msgs` (records carrying a book level). A hard socket kill was already
      caught at ~41s by databento's own monitor, so this clock's unique value is narrower than first
      claimed: gateway-side data stalls and wedged callback threads.
      ✅ **The ack check now raises at any hour** — settled 07-27 by probing the real gateway off-hours.
      It used to infer the ack from `len(symbology_map)` and hedge to RTH-only; measured, the gateway
      acks in ~17ms overnight while `symbology_map` stays empty, so the old check fired a FALSE "never
      acked" and the hedge was the only thing stopping a 60s restart loop. Keyed on
      `SystemCode.SUBSCRIPTION_ACK` it is honest at every hour. (The `_quote_ring` thread race queued
      onto the same check is **closed 07-27**: the ring is deleted and the NBBO rides on the print.)
      ⏳ Remaining for arming: the **intraday-replay run** through `tools/dbn_live_probe.py` — a weekday
      after the close, replaying that day's 09:30→16:00 on the real watchlist. No deploy required.
- [x] **0.2 Corpus-gap check. — DONE 2026-07-27.** `tools/flow_corpus_gaps.py`, exits 1 on any gap.
      Absent / not-rolled-at-current-`SCHEMA_VERSION` / **truncated** — the third because the
      backfill's skip guard is `exists() and size > 0`, so a partial download is skipped forever and
      looks finished (it has bitten twice: the eight 7 KB defs files, and 2025-09-02 at 64.5 MB against
      a ~270 MB median). Calendar derived from `equity_bars`, not hardcoded.
      **First run found three sessions with NO definition file** — 2025-08-07, 2026-04-28, 2026-07-09 —
      behind a defs pass reporting 258/260 done. A session without definitions cannot be rolled at all,
      so all three would silently never have entered the corpus.

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
- [x] **1.2 `mny_bucket='unknown'` pooled two different things. — DONE 2026-07-27.** Split into
      `no_spot_index` and `no_spot_missing` at `schema_version=4`. **The pooled bucket was 38.75% of
      session premium and 99.4% of it was structural**: `no_spot_index` $10,129.6M vs
      `no_spot_missing` $64.9M on 2025-07-28, a 156× split hiding inside one label.
      OPRA's definition record carries **no** discriminator — `cfi` is empty, `security_type` is
      `'OPT'` and `underlying_product` is `255` on all 1,592,395 contracts (verified) — so
      `INDEX_ROOTS` is a curated list in `db/flow_corpus.py`. Being incomplete is safe *by
      construction*: a missed root falls into `no_spot_missing`, which the re-roll logs as
      `no_spot_$` and would jump from ~$65M to billions. The missing arm is already earning its
      keep — it surfaces **BRKB** (BRK.B ticker format), **FI** (renamed from FISV) and **PARA**
      (acquired), i.e. item 1.4 arriving on its own. `no_spot_prints` in the session ledger now
      counts the missing arm only, so it reads 0.6% instead of 14% and is a usable alarm.
- [x] **1.3 Sweeps counted as N observations. — DONE 2026-07-27.** Added an `orders` column:
      distinct `(ts_event, instrument_id)` groups. **`orders`, never `prints`, is the N for any
      z-score or standard error**; `prints` stays as registered. 6,930,544 prints → 6,464,608 orders
      (6.72% pooled) — but the inflation is *concentrated*: median 5.85%, p90 9.44%, max 15.97%
      across the 480 names with ≥1000 prints, worst in thin heavily-swept names (TLRY, SNAP, CIFR,
      BITF, POET). At p90 that overstates √N by 1.051×, **in exactly the tier 1.7 expects false
      positives in.** ⚠️ Deliberately **not** the "one sign per ns-group" half of the original fix:
      measured first, and legs disagree on side in only 1.371% of multi-print groups carrying
      **0.167% of premium** — not worth overriding a registered per-print definition for. A group
      that does straddle two buckets is counted in each, so `SUM(orders)` across buckets exceeds the
      session's group count by 0.13%; correct per bucket, wrong as a session total.
- [ ] **1.4 Declare the delisting/acquisition rule.** Acquired names are the archetypal winners and
      their bars simply stop. *Fix:* write the terminal-value rule into the prereg **before**
      grading. *Done when:* it is registered in Addendum F (the amendment; E = the 07-27 multileg redefinition).
      *Now has a surface, and it is better than expected:* 1.2's `no_spot_missing` bucket enumerates
      exactly these names per session **with premium attached**, so the rule can be written against an
      observed list instead of a guess. Measured across 37 sessions the bucket runs $54M–$574M (median
      $123M) and **the spikes are corporate actions**: the top session is **WBA at $386M**
      (2025-08-27 — Walgreens taken private, equity bars stop while options keep trading), then SATS
      $70.5M (EchoStar) and BRKB $37.3M (ticker format, not a corporate action — worth fixing
      separately). This is the "acquired names are the archetypal winners" case arriving with a
      dollar figure on it.
- [~] **1.5 Contract multiplier — BOUNDED 2026-07-27, register in Addendum F (the amendment).**
      `premium = price/PX * size * 100.0` hardcodes 100 and adjusted contracts do not always deliver
      100 shares. **No field in the OPRA definition record can fix it** — every candidate is UNDEF
      on all 1,592,395 C/P contracts: `contract_multiplier`, `original_contract_size` and
      `min_lot_size_round_lot` are all INT32_MAX, `unit_of_measure_qty`, `display_factor` and
      `price_ratio` are all INT64 UNDEF (exhaustively checked, not just the one field the research
      round named). **But the exposure is now measured rather than unknown**: adjusted contracts are
      detectable by the OCC convention of a digit-suffixed root (ENVX1, BABA2, CVX1) and are 225
      roots / 1,897 prints / **$7.2M = 0.028% of session premium**. That is a bounded known
      limitation, not a threat to any result. *Remaining:* register the bound + the digit-suffix
      detector in Addendum F so nobody re-opens it.
- [x] **1.6 Re-roll every session** at the new `schema_version`. *Done when:* `ingested_dates()`
      returns the full corpus at the current version. **DONE — verified 2026-07-28: 251/251
      sessions at `schema_version=6`** (the freeze note below was written at v4; v5 and the v6
      label migration each carried their own re-roll).
      **The schema is now FROZEN at v4** — 1.1/1.2/1.3 were the schema-affecting fixes and all three
      have landed, so this is one pass, not two. Blocked only on the tcbbo backfill (71/260 at
      2026-07-27 01:00, ~0.8 min/session). One session rolls in 13s, so 260 is ~1 hour.
      **Do not add a column after this point without accepting a second full re-roll.**
- [x] **1.6b Condition-label backfill → v6 re-roll (Addendum E, founder-blessed 07-27).** Capture
      every corpus session's Alpaca OPRA trades (with `c`) to raw per-session files, join per the
      frozen method, then re-roll the corpus at `schema_version=6` with code-based `multileg`.
      **All-or-nothing:** v6 only when every session's label file clears the ≥98% join floor — a
      mid-window definition seam is the Addendum B failure. 1.8 re-runs at v6. Until then v5
      readings carry the floor-not-fence caveat.
      ✅ **CLOSED 2026-07-28 — but the all-or-nothing floor above was SUPERSEDED, not met.** v6
      landed on all 251 sessions (`multileg_source=codes` everywhere), with join coverage min
      77.0% / avg 97.4% / max 99.6% — **exactly 115 sessions clear ≥98%**. Rather than block the
      migration, **Addendum F** registered that 115-session set as the coverage-gated robustness
      subset and requires the coverage distribution to be reported alongside every v6 reading.
      1.8 re-ran at v6 and overturned its own earlier verdict (see below). Read this item as: the
      floor became a *reported gate*, never a silent pass.
      **Machinery BUILT + verified 07-27 (backfill still running):** the frozen join lives in ONE
      place (`tools/condition_labels.py`), consumed by both the floor-check
      (`tools/condition_label_join.py --sweep` → `data/alpaca_conditions/join_ledger.json`, THE
      migration gate) and the rollup. The v6 switch is one line — `SCHEMA_VERSION = 6` in
      `db/flow_corpus.py` — which atomically activates code-based multileg, the raw-code `cond`
      grain (row key), per-session `multileg_source`/`label_join_rate` audit columns, and the
      table rebuild. End-to-end verified on scratch DBs: premium totals match v5 exactly (drift 0);
      2025-07-28 join 98.40% PASS, true package premium **58.13% vs heuristic 33.33%** — the
      in-sample session Addendum D's "33%" was measured on; cond-grain row expansion 1.66×.
      Remaining: backfill completes → sweep all 250 → flip the constant → re-roll → 1.8 at v6.

### 1b. The four measurements

Each decides something downstream. Run them, do not skip to building.

- [~] **1.7 (a) Is `|z| ≥ 2` a liquidity detector?** Shuffle print signs randomly, run the exact
      registered rule on real data, count firing rate per liquidity quintile. Zero signal in, so
      anything that fires is a false positive. *Decides:* whether the mid-cap sub-hypothesis is
      testable at all. *Note:* the alarming 12× figure is currently a **simulation** — this replaces
      it with a measurement. Change no threshold before this runs.

      **1.7 METHOD + CRITERIA — REGISTERED 2026-07-28, BEFORE THE RUN** (`tools/z_null_probe.py`):
      - **Null:** randomize each **ORDER's** buy/sell classification, not each print — a sweep is
        ONE decision, and shuffling per print would fake independence the tape does not have
        (the corpus carries `orders` and `premium_sq_orders` for exactly this reason).
      - **Construction:** for a name-day, null tilt numerator = Σ εᵢpᵢ (εᵢ = ±1 per order),
        denominator = Σpᵢ ⇒ E[tilt] = 0 and Var(tilt) = Σp²/(Σp)² = 1/N_eff, where
        **N_eff = (Σp)²/Σp²** is the effective number of independent orders (inverse
        participation ratio). N_eff is computed EXACTLY from the rollup; the null tilt is drawn
        as Normal(0, 1/√N_eff).
      - **Rule applied exactly as registered** (no re-derivation): trailing 20 days of the name's
        own null tilt excluding today, `z = (tilt − mean₂₀)/std₂₀`, ≥15 of 20 days present,
        **liquidity floor ≥ $250k** option premium on the day, event `|z| ≥ 2.0`. Both registered
        arms reported (ALL and SHORT_OTM).
      - **Liquidity quintiles:** by each name's MEDIAN daily total option premium across the
        corpus, so a name holds one quintile rather than migrating day to day.
      - **PASS (the rule is not a liquidity detector):** least-liquid quintile firing rate
        **< 2.0×** the most-liquid quintile's.  **FAIL:** ≥ 2.0×.
      - ⚠️ **Registered asymmetry in how the result may be read.** The normal draw matches the
        null's variance exactly but UNDERSTATES its tails on days when one order dominates —
        precisely the illiquid case. So a **FAIL is decisive** (the artifact shows up even under
        the conservative null), while a **PASS is provisional** and must be confirmed by an exact
        per-order sign shuffle on a ≥20-session subsample before 1.7 is closed. Stating this now
        so a comfortable answer cannot be banked without the follow-up.

      **1.7 RESULT — RAN 2026-07-28** (6,998 names × 251 sessions, 20 null universes, seed
      20260728). **ALL arm: Q1/Q5 = 0.38× ⇒ PASS (provisional).** Firing rate rises monotonically
      WITH liquidity — Q1 3.261% (460 tested), Q2 3.648%, Q3 5.638%, Q4 8.302%, Q5 8.522%
      (4,489,500 tested), pooled 8.445%. The registered `< 2.0×` bar is cleared in the
      *opposite* direction from the one feared. **Three things the headline number hides —
      all three matter more than the verdict:**
      - ⚠️ **The PASS is bought by the $250k floor, not by the z-machinery — so the floor is
        load-bearing and may not be moved without re-running this.** Measured on the same
        aggregate: the floor admits **0.105% of Q1 name-days** (84 of 79,974; only 59 of 1,399
        Q1 names EVER clear it) vs **75.1% of Q5's** (243,576 of 324,261). The rule was
        therefore never tested on a genuinely illiquid name-day — it was tested on illiquid
        names having a 500×-normal day, which are not thin at all. Mechanism for the inversion:
        such a day has huge N_eff (small σ) against a 20-day baseline of tiny-N_eff days (large
        σ), so today's tilt is small relative to its own baseline std and `|z|` is SUPPRESSED.
      - ⚠️ **SHORT_OTM produced NO verdict — Q1 tested = 0, so the registered ratio is
        undefined and this arm is NOT answered.** Not a floor effect alone: 22 Q1 name-days
        clear the floor with short-OTM premium present, but none carry ≥15 of 20 prior days
        with short-dated OTM flow, so the baseline requirement voids them. Reported Q2 7.444%
        (900 tested) / Q3 5.999% / Q4 6.673% / Q5 8.080%, pooled 7.932%; Q2/Q5 = 0.92×, on 45
        real name-days — too thin to substitute for the registered Q1 comparison. **Closing
        1.7 requires an arm-specific design that can actually populate the illiquid bucket.**
      - **The null base rate is 8.4%, not 5%** — the registered `|z| ≥ 2` gate fires on ~8.4%
        of eligible name-days with ZERO signal present. Expected for this estimator, not a bug
        (a 20-day sample mean/std with the biased variance divisor makes `z` ≈ t₁₉·1.05, and
        `k` as low as 15 fattens it further ⇒ ~7-8%). **Consequence for Phase 1: any observed
        event rate must be scored against 8.4%, and any win-rate claim must net out a false-
        positive floor of that size.** Not previously accounted for anywhere.
      - **Inverted follow-up for 2.4/§4, not a failure:** the rule is *under*-sensitive exactly
        on a name's first big day — the "unusual activity" case the product most wants to catch.
      **Status: OPEN.** Registered asymmetry stands (PASS is provisional ⇒ exact per-order
      shuffle on ≥20 sessions), and the SHORT_OTM arm is additionally unanswered.

      **1.7 ADDENDUM A — REGISTERED 2026-07-28, BEFORE THE CLOSING RUN.** The 07-28 result
      above stands unedited; this adds the two runs that close it. **Nothing about the rule
      changes — `|z| ≥ 2.0`, the 20-day baseline and the ≥15-present requirement are all held
      fixed.** The only change is to the TEST's eligibility gate, because the per-day $250k
      floor made the illiquid bucket unpopulated and therefore made the question unaskable.
      Threshold recalibration is **explicitly out of scope**: tuning `z` against alert volume
      before the signal is graded would tune a parameter against an outcome we have not
      measured. The 8.4% null rate's role in this study is **subtraction at grading time**,
      not a dial (founder call 2026-07-28; the flow z is also unrelated to
      `FUSION_Z_GATE_T = 3.5` in `signals/fusion/scorer.py`, which is a score-normalization
      dial and not a sigma count — the shared digits are coincidence).
      - **A1 — universe variants.** Re-run the registered rule under a cross of eligibility
        gates: day floor ∈ {$250k, none} × name floor ∈ {none, median daily total premium
        ≥ $100k}. Quintiles are recomputed WITHIN each surviving universe. Both arms.
        Reported for each: eligible name-days/session, firing rate per quintile, Q1/Q5.
        ⚠️ **Honesty note — the ALL-arm answer is already known** from an exploratory sweep
        run 2026-07-28 *before* this registration (no floor: **1.04×**; $50k/$100k name
        floors: 0.98×/0.99×; with the day floor retained: 0.32×/1.07×). A1 is therefore a
        **confirmation under the full registered machinery, not a blind test**, and is
        recorded as such. The genuinely unknown part of A1 is SHORT_OTM, whose Q1 was empty.
      - **A2 — the exact per-order shuffle** (the follow-up the original registration
        demanded; this is blind). Rebuild per-ORDER premiums from the raw tcbbo DBN for the
        **40 most recent corpus sessions** (all 261 are on disk), giving 20 tested days after
        the 20-day baseline burn-in. Assign each order an independent ε = ±1 with p = 0.5 —
        no normal approximation — recompute tilt = Σεᵢpᵢ / Σpᵢ, and apply the registered rule.
        Orders are the rollup's own grain: distinct `(ts_event, instrument_id)` groups.
        *Window note (2026-07-28, during the run):* market holidays leave an 87-byte tcbbo
        file that the naive glob counted as a session — Juneteenth 2026-06-19 sat inside the
        window, and since the 20-day burn-in eats half of it, that silently yields **19**
        tested days against a registered **≥20**. Sessions are now selected by file size
        (> 1 MB), not by calendar, so the window is 40 REAL sessions: **2026-05-29 …
        2026-07-27**. Also measured while extracting: the `size` UNDEF sentinel runs
        ~45–47k prints/session and is now dropped by an EXPLICIT guard with the count
        logged, rather than relying on the incidental definitions-join protection.
      - **PASS/FAIL for the close (both must hold):** (1) A1's Q1/Q5 **< 2.0×** in the
        no-day-floor universe for BOTH arms, with Q1 non-empty in each; (2) A2's pooled
        firing rate and Q1/Q5 agree with the normal-draw null to within **±2 percentage
        points** and **±0.5×** respectively. A2 disagreeing beyond that means the normal
        approximation was hiding tail behaviour and **1.7 fails** regardless of A1.

      **A1 RESULT — RAN 2026-07-28.** Criterion (1) **MET, both arms, with Q1 populated.**
      | day floor | name floor | universe | ALL Q1/Q5 | SHORT_OTM Q1/Q5 |
      |---|---|---|---|---|
      | $250k | none | 6,998 | 0.38× | Q1 EMPTY |
      | none | none | 6,998 | **1.07×** | **1.11×** |
      | $250k | $100k | 1,437 | 1.07× | 0.91× |
      | none | $100k | 1,437 | 1.00× | 1.04× |
      With the day floor removed, ALL's Q1 goes from 460 tested name-days to **407,120** and
      SHORT_OTM's from **0 to 13,000** — the buckets the original run could not populate.
      Every populated variant sits at 0.91–1.11×, i.e. **the rule is liquidity-neutral on its
      own merits, not because a filter was shielding it**; the 0.38× was the day floor
      distorting the sample. Pooled null rate is 8.3–8.6% throughout, unchanged by any gate,
      which re-confirms it as a property of the 20-day estimator (see the base-rate note above).
      - **Floor choice is a PRODUCT decision, not a correctness fix** (measured, so it stops
        being re-litigated): median daily premium and median N_eff rank names near-identically
        (Spearman **0.93**), and the 1,437 names a $100k premium floor keeps have median
        N_eff 36.5 — the premium floor was selecting on order count indirectly. Since the
        no-floor variant already passes, no floor is load-bearing for the TEST. Candidate
        operating universes if one is ever wanted: N_eff ≥ 5 → 2,788 names; ≥ 10 → 1,919;
        ≥ 25 → 996. Contract volume was NOT measured (absent from the cached aggregate;
        an 8-min rescan if it is ever wanted as a third ranking).

      **A2 RESULT — RAN 2026-07-29. ⛔ 1.7 FAILS its registered closing criterion.** 40
      sessions (2026-05-29 … 2026-07-27), ~380M orders rebuilt from raw tcbbo, 20 draws,
      every order given a real ±1 — no normal approximation anywhere.
      | arm | pooled exact | pooled normal | Δ | Q1/Q5 exact | Q1/Q5 normal | Δ | verdict |
      |---|---|---|---|---|---|---|---|
      | ALL | 7.362% | 8.983% | 1.62pt ✓ | **0.24×** | **1.04×** | **0.80× ✗** | **FAIL** |
      | SHORT_OTM | 8.580% | 9.860% | 1.28pt ✓ | 0.00× | 0.00× | — | **degenerate, NOT a pass** |
      The registered bar was ±2.0pt pooled AND ±0.5× on Q1/Q5. ALL breaches the ratio leg,
      and per the registration ("A2 disagreeing beyond that means the normal approximation
      was hiding tail behaviour and 1.7 fails regardless of A1") **1.7 is FAILED and stays
      open.** The asymmetry registered on 07-28 did exactly the job it was written for.
      - **What actually broke: the normal approximation overstates false alarms in thin
        names by ~4×.** Exact vs normal by quintile (ALL): Q1 **2.129% vs 9.020%**, Q2 3.386
        vs 9.046, Q3 6.282 vs 9.332, Q4 8.810 vs 8.987, Q5 **8.927 vs 8.680**. The two nulls
        agree where there are many orders and diverge where there are few — the tilt of a
        3-order day is a handful of discrete values, not a bell curve, and a 20-day baseline
        built from lumpy ±1s has a std wide enough that today rarely clears 2σ.
      - ⚠️ **The base rate is NOT one number — it is a curve in liquidity, and it is lower
        than 1.7 reported.** True pooled null is **7.36%**, not 8.4–9.0%, and it runs from
        **2.1% (thinnest) to 8.9% (most liquid)**. Every prior statement of "≈8.4% regardless
        of liquidity" came from the normal null and is **superseded**: grading must subtract a
        PER-QUINTILE base rate, or thin-name signal will be scored against a bar ~4× too high.
      - **The substantive liquidity-detector question is answered MORE strongly, not less.**
        Exact Q1/Q5 = 0.24× against a 2.0× bar: the rule does not over-fire on thin names, it
        **under**-fires on them. A1's finding survives; what died is the approximation used
        to reach it.
      - **SHORT_OTM's "PASS" is spurious and is not banked.** Q1 = 60 tested name-days, 0
        fired, so 0.00× vs 0.00× is nothing compared to nothing. Structural, not a bug: the
        ≥15-of-20 presence rule needs a thin name to carry short-dated OTM flow on 15 of 20
        days, which a 40-session window cannot supply (A1 got 13,000 Q1 name-days only by
        spanning all 251). **A2's SHORT_OTM ratio is unevaluable at this window length.**
      - *Defect on the first A2 attempt (recorded, not hidden):* it ran with the $250k day
        floor still wired in, reproducing the exact pathology A1 diagnosed (Q1 = 20 tested,
        SHORT_OTM Q1 = 0) and returning a meaningless ALL "PASS" of 0.00× vs 0.00×. Re-run on
        the A1-corrected universe is the result above; the day floor is now a CLI flag
        defaulting to the no-floor universe so the two addenda cannot drift apart again.
      - **To close 1.7 now needs:** (a) a SHORT_OTM window long enough to populate Q1 — the
        exact shuffle over ≥120 sessions, since 40 provably cannot; and (b) a decision on
        whether the per-quintile exact base rate replaces the flat one in the grading spec
        (it should — but that is a registered change, not an edit). **(b) is now drafted as
        1.23**, founder-asked 07-29; (a) and 1.23(c) are the SAME long run, so one job closes both.

      **1.7 ADDENDUM B — REGISTERED 2026-07-29, BEFORE THE FULL-WINDOW RUN.** Everything above
      stands unedited. ⚠️ **This run cannot un-fail 1.7 and is not an attempt to.** The A2
      criterion was breached on 07-29; that FAIL is permanent and the normal-vs-exact agreement
      bar is **not re-tested here** — re-running a failed criterion at a new window until it
      passes is precisely the fishing the prereg law exists to stop. What this run does is
      supply the two things that are still genuinely unknown: **1.23(c)'s base-rate table on the
      real grading window**, and the **SHORT_OTM** Q1/Q5 question that 40 sessions provably could
      not populate.
      - **Window:** all **251** real sessions on disk, **2025-07-28 … 2026-07-27** (one full
        year; 261 files, 10 holiday stubs excluded by the >1 MB size filter). 231 tested days
        after the 20-day burn-in. Definitions verified present for all 251 before registering.
      - ⚠️ **This does NOT spend the untouched test split.** The shuffle consumes **no outcomes**:
        it randomizes order signs and counts how often the DETECTOR fires. No forward return, no
        post-event move, nothing after the session is read — the only price touched is the PRIOR
        close, for moneyness bucketing. So the 2026-06-01 → 2026-07-25 split stays free despite
        the window covering it. Recorded because it is the obvious thing to challenge later.
      - **(B1) SHORT_OTM liquidity neutrality.** Exact-null **Q1/Q5 < 2.0×** with **Q1 ≥ 1,000**
        tested name-days — the bar A1 used for ALL. If Q1 still falls short at a full year, the
        arm is declared **unevaluable at any window we possess** and reported as such; it is NOT
        retried at a longer window we do not have.
      - **(B2) Stationarity — registered so this run CAN produce a negative result.** The table
        is reported on the full window **and** on two halves (first 125 vs last 126 sessions).
        If any quintile's exact rate moves **> 2.0 percentage points** between halves, the base
        rate is declared **non-stationary** and 1.23(c) is amended from a fixed table to a
        window-matched one BEFORE any grading uses it. A wobbly table must not be presentable
        later as a stable one.
      - **(B3) Draw noise is reported, not hidden.** 20 draws. Report each quintile's standard
        error across draws; any quintile with across-draw SE > 0.5pt carries that SE in the
        table rather than being quoted as a point estimate.
      - *Mechanics, declared up front:* seed **20260729**, and the draw loop is inverted
        (sessions outer, draws inner) so memory is ~0.7 GB instead of ~30 GB. That changes the
        random stream, so this is a **fresh run, not a reproduction** of A2's numbers. A2 stays
        reproducible: it keeps `tools/order_grain_null.py` unmodified and this runs in a
        separate `tools/base_rate_table.py`.

      **EXTERNAL AUDIT — RECEIVED 2026-07-29, DISPOSITION VERIFIED BEFORE ACTING.** An independent
      adversarial audit of the whole flow method. Each claim was re-checked against code and data
      rather than accepted; the dispositions below are what survived that check.
      - ⛔ **ACCEPTED — the axis defect (audit Findings 1/2). This is the real one.** The prereg
        registers a **call/put directional** tilt (`bull = call_buy + put_sell`,
        `FLOW_BASELINE_PREREG_2026-07-26.md:68-72`), but the real-statistic code paths compute
        **aggressor buy − sell with `right` ignored** — `tools/multileg_arm_probe_v6.py:11` is
        literally `net = sum(premium|side=buy) - sum(premium|side=sell)`. **Measured on the full
        corpus** (1,073,060 name-days, all 251 sessions), the two axes are different statistics:
        corr **+0.404 / +0.235 / +0.102** and opposite-sign **31.7% / 37.0% / 39.4%** at premium
        floors none / $250k / $1M. ⚠️ **The disagreement WORSENS with size** — on the biggest
        name-days, the ones carrying the signal, the axes are nearly uncorrelated and point
        opposite ways ~2 days in 5. Every conclusion drawn from the aggressor axis is a
        conclusion about a statistic the prereg does not register.
      - ✅ **Cost of the fix is small, and that is a measured fact, not an estimate:** `right` is
        already a stored rollup column (`db/flow_corpus.py:92,135,219`; the rollup key packs
        `n_r = 2` at `tools/flow_corpus_rollup.py:396-398`). The registered tilt is therefore
        computable **from existing rollups — no re-ingest, no re-extraction.** What must be
        re-run is 1.8's probe and `corpus_direction`, not the nulls.
      - ❌ **REJECTED — that the nulls (1.7 / A2 / Addendum B) measured the wrong statistic.** The
        audit concludes "any noise floor, Q1/Q5, or later grading applied to call/put tilt is
        unmeasured." That is wrong **structurally, not by degree.** The null never reads `side`
        or `right`: it is built from unsigned per-order premium and a coin flip. Both axes are
        `Σsᵢpᵢ / Σpᵢ` with `sᵢ ∈ {±1}` — registered uses `s = side_sign × right_sign`, aggressor
        uses `s = side_sign` — and replacing `s` with a symmetric Rademacher ε makes them the
        **same process**, since `ε × right_sign` is distributed identically to `ε`. Denominators
        match too: `bull + bear` partitions exactly the classified set that `buy + sell` does.
        Quintiles, presence, `N_eff` and the SHORT_OTM subset are premium/bucket-defined and so
        do not move either. ⇒ **the noise-floor curve transfers to the registered tilt exactly**,
        and Addendum B's run is not invalidated. Recorded with the argument so this is not
        re-litigated from the audit text alone.
      - ★ **NEW, and worth more than the finding it was filed under:** within-order cancellation
        IS axis-dependent, and nobody had this. A straddle (call buy + put buy) is one sign on
        the aggressor axis but two opposing signs on the registered axis. The null models each
        order as a single signed decision carrying its whole premium — an assumption that fits
        the two axes differently, on a corpus where packages are ~56% of premium. Measurable;
        queued rather than argued.
      - ⛔ **ACCEPTED — audit Finding 5, and it changes Addendum B's runner before it ships.**
        Prereg registers quintiles by **trailing-60-day median** premium
        (`FLOW_BASELINE_PREREG_2026-07-26.md:149`); `z_null_probe.py` and `order_grain_null.py`
        both use a **whole-corpus median fixed per name**. Implementation ≠ registration, and the
        cut-points are conditioned on the full sample including the reserved test window. The
        1.23(c) table is INDEXED BY QUINTILE, so this lands on it directly: the table must be
        built under the registered trailing-60 rule. Caught before the table was produced.
      - ⛔ **ACCEPTED — audit Finding 4: registered clauses that were never implemented.** No
        demeaning by the whole-market daily mean (Addendum A), no `spread > 50% of mid` print
        drop, and no `>20%`-dropped name-day exclusion (both prereg:60-61) exist anywhere in
        `tools/flow_corpus_rollup.py` — `good_q` tests null/zero/crossed only. Each must be
        implemented or **explicitly retired by addendum** before grading; until then "we followed
        the prereg" is not a claim we may make about these three clauses.
      - ✅ **ACCEPTED as autopsy — audit Finding 3.** The tilt is bounded to [−1, 1] but the
        normal draw is not: at `N_eff ≈ 1.4`, σ ≈ 0.845 puts ~24% of draws outside the support
        the real statistic can even occupy, sorted by liquidity exactly where exact and normal
        diverged. Does not un-fail 1.7 (already FAILED); it sharpens **why** the approximation
        died, and any future normal shortcut must respect the bound or not be used.
      - **Withdraw list — 2 of 3 accepted.** (i) "the rule is liquidity-neutral" (A1 RESULT
        wording) — **withdrawn**; the exact null says 0.24×, which is *under*-firing on thin
        names, not neutrality. (ii) 1.8's single-name unreadability claim, insofar as it is read
        as applying to directional tilt — **withdrawn pending re-run on the registered axis**.
        (iii) "1.7's per-quintile rates are floors only for aggressor-imbalance z" — **rejected**,
        per the axis-invariance argument above.

      **ADDENDUM B RESULT — RAN 2026-07-29.** `tools/base_rate_table.py`, all **251** sessions
      (2025-07-28 … 2026-07-27), 20 draws, quintiles on the prereg's trailing-60-day median cut
      cross-sectionally per day. ⚠️ `tested` is summed over draws — divide by 20 for real
      name-days. **1.7 remains FAILED; this closes its open sub-questions, it does not reopen the
      verdict.**
      | tier | tested | fired | **ALL rate** | SE | SHORT_OTM rate | SE |
      |---|---|---|---|---|---|---|
      | Q1 least liquid | 394,280 | 7,731 | **1.961%** | 0.11pt | 2.353% | **2.73pt** |
      | Q2 | 1,878,060 | 51,356 | **2.735%** | 0.05pt | 3.143% | **0.51pt** |
      | Q3 | 4,153,260 | 198,463 | **4.778%** | 0.03pt | 3.389% | 0.11pt |
      | Q4 | 5,137,180 | 398,744 | **7.762%** | 0.03pt | 5.450% | 0.04pt |
      | Q5 most liquid | 5,365,960 | 463,032 | **8.629%** | 0.03pt | 8.207% | 0.03pt |
      | pooled | 16,928,740 | 1,119,326 | **6.612%** | — | 6.958% | — |
      - **(B1) ALL: MET.** Q1 = 394,280 tested (19,714 real name-days), Q1/Q5 = **0.23×** against
        a 2.0× bar. A1's substantive finding is confirmed at full length on the registered
        quintile rule: the detector **under**-fires on thin names, it does not over-fire.
      - ⛔ **(B1) SHORT_OTM: NOT MET, and per the registration it is now CLOSED, not retried.**
        Q1 = 680 tested = **34 real name-days**, below the registered 1,000 floor. The nominal
        0.29× is not banked: Q1's across-draw SE is **2.73pt on a 2.35% rate**, i.e. the estimate
        is noise. Registered disposition applies verbatim — **unevaluable at any window we
        possess**; a full year is all there is, so this is not deferred to a longer one.
        *Implementation ambiguity disclosed rather than quietly fixed:* the 1,000 bar is checked
        against the draw-summed count, which is more lenient than the registered wording; at 680
        vs 34 the verdict is NOT MET under either reading, so it did not change the outcome.
      - ✅ **(B2) STATIONARY, both arms** — max half-vs-half quintile gap **0.29pt** (ALL) and
        **0.63pt** (SHORT_OTM) against a 2.0pt bar. ⚠️ **This contradicts the prediction made
        when the run was launched.** The tape roughly doubled across the year (Aug-2025 ~7.4M
        prints/session → Jul-2026 ~12.3M), and the expectation on the record was that the floor
        would drift up with it. It did not, and the reason is the audit's own Finding 5: making
        quintiles **relative** (cross-sectional, per day, trailing-60) means growth moves all
        names together and tier membership renormalizes daily. **The noise floor is a property of
        a name's liquidity RANK, not its absolute order count.** Under the old whole-corpus-median
        rule, growth would have dragged names across fixed cut-points and manufactured exactly the
        drift that was predicted — so fixing F5 is what bought the stationarity.
      - **(B3)** ALL: no quintile exceeds 0.5pt across-draw SE. SHORT_OTM: **Q1 and Q2 flagged**
        and carry their SE in the table above; they are not to be quoted as point estimates.
      - **⇒ 1.23(c) is SATISFIED for the ALL arm** — the table above is the base rate grading
        subtracts against, on the grading window, under the registered quintile rule. For
        **SHORT_OTM Q1/Q2 there is no usable base rate**, so per 1.23(d) those cells are declared
        **unevaluable in advance** rather than graded against a noisy floor.
      - Consistency with A2's 40-session run (pooled 7.362%, Q1 2.129%, Q5 8.927%): same shape,
        modestly lower at full length — **~2.0% thinnest → ~8.6% most liquid, a 4.4× spread.**
        The curve is confirmed, not overturned.

      **CONFOUND ISOLATED 2026-07-29 — and the headline was stable for a BAD REASON.** Addendum B
      changed the window (40 → 251) and the quintile rule (whole-corpus → trailing-60) in the same
      run, so neither could be credited. Re-ran the 251-session window under A1/A2's whole-corpus
      rule (`--quintile-rule whole`), everything else held fixed:
      | run | Q1 | Q5 | Q1/Q5 | pooled |
      |---|---|---|---|---|
      | 40 sessions, whole-corpus (A2) | 2.129% | 8.927% | 0.24× | 7.362% |
      | 251 sessions, whole-corpus | 2.861% | 8.479% | **0.34×** | **6.612%** |
      | 251 sessions, trailing-60 (registered) | 1.961% | 8.629% | **0.23×** | **6.612%** |
      - ✅ **Pooled is bit-identical between the two rules** — same 16,928,740 tested and 1,119,326
        fired. Correct by construction (the rule re-partitions name-days into tiers, it cannot
        change which fire) and a useful implementation self-check. ⇒ **the pooled 7.362% → 6.612%
        drop is 100% the WINDOW**, proven rather than argued.
      - ⚠️ **The Q1/Q5 headline barely moved (0.24× → 0.23×) because two LARGE effects nearly
        cancelled.** Window alone: 0.24× → 0.34× (less steep). Rule alone: 0.34× → 0.23× (more
        steep). Reporting the endpoints as "stable" would have implied a robustness that does not
        exist — recorded because that is exactly the false comfort this run was built to catch.
      - **The registered rule is the better ESTIMATOR, not merely the compliant one.** It yields a
        monotone SHORT_OTM curve (3.143% < 3.389% at Q2/Q3) where the whole-corpus rule inverts
        (4.907% > 4.077%). Mechanism: a whole-corpus median files a name in one tier for the year,
        so names whose liquidity changed are systematically misfiled; daily relative ranking puts
        each name-day in the tier it actually occupied that day.
      - **B1's SHORT_OTM NOT-MET verdict is robust to the rule:** Q1 = 48 real name-days under
        whole-corpus vs 34 under trailing-60, both far under the registered 1,000 bar.
- [x] **1.8 (b) Is the multi-leg sign flip real, or just SPX? — RE-ANSWERED AT v6 2026-07-28:
      IT REACHES SINGLE NAMES.** The corpus-wide re-run on code labels
      (`tools/multileg_arm_probe_v6.py`, all 251 sessions, rollup grain) **overturns the
      heuristic-era verdict below.** True package share of single-name premium is **40.9%**
      (heuristic said 19.5% — recall ~51% hid half the legs in the "clean" arm), and the
      single-name **net** measure FLIPS pooled: all −$13.7B vs ex-multileg **+$48.3B** (79/251
      sessions flip; clip and raw stay same-sign, 1/3 flips). Same verdict on the registered
      ≥98%-coverage robustness arm (115 sessions: −$7.5B vs +$19.8B) — not coverage-driven.
      Demonstration on the original in-sample session: heuristic clean arm −$89.7M (same sign),
      code clean arm **+$217.4M** (flip) — the exact print-for-print reversal the missing recall
      predicted. Consequence: **Addendum D's package fence applies to single names too**; pooled
      single-name net flow is unreadable without splitting the package grain. Index/ETF stays
      worst (69.6% package premium, flips 2/3). Coverage gate per Addendum F honored in the
      tool's output (12 sessions <95% flagged; bias caveat applies). *Heuristic-era result kept
      below for the record — do not cite it.*

      ⚠️ **RE-ASKED ON THE REGISTERED AXIS 2026-07-29 — THE SIGN-FLIP CONSEQUENCE IS WITHDRAWN
      FOR SINGLE NAMES.** Everything above was measured on aggressor `buy − sell`
      (`multileg_arm_probe_v6.py:11` has no `right` in its query), not the prereg's call/put
      tilt. `tools/flow_tilt.py --mode netflip` re-runs the identical cells — same arm split
      (`INDEX_ROOTS | ETF_SET`), same 251 sessions, same rollup grain — on both axes:
      | arm | axis | pooled ALL | pooled CLEAN | flip | per-session |
      |---|---|---|---|---|---|
      | single-name | **dir (registered)** | +$27.9B | +$11.4B | **no** | 48/251 (19.1%) |
      | single-name | side (aggressor) | −$13.7B | +$48.3B | FLIPS | 79/251 (31.5%) |
      | index/ETF | **dir (registered)** | −$93.4B | +$9.5B | FLIPS | 118/251 (47.0%) |
      | index/ETF | side (aggressor) | −$95.0B | +$20.3B | FLIPS | 112/251 (44.6%) |
      The `side` row reproduces this card's committed figures **to the decimal** (−13.7B →
      +48.3B, 79/251) from an independent code path, which is what makes the `dir` row
      trustworthy rather than a rival implementation's opinion.
      - **Withdrawn:** "pooled single-name net flow is unreadable without splitting the package
        grain," insofar as it is read as a claim about *direction*. On the registered axis the
        single-name pooled sign **does not flip**.
      - **Stands, and is not softened:** packages are ~56% of premium and remove **59%** of the
        single-name directional net (+$27.9B → +$11.4B). The package fence remains mandatory for
        MAGNITUDE and for every side-weighted measure. What died is the sign argument, not the fence.
      - **Stands unchanged:** index/ETF flips on BOTH axes (47.0% / 44.6% of sessions), so the
        fence is unambiguous there. The 40.9% package-share and ~51%-recall findings are
        axis-independent and survive intact.
      ~~ANSWERED 2026-07-27: JUST INDEX/ETF.~~
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
- [x] **1.9 (c) Validate the nanosecond multi-leg heuristic — GROUND TRUTH FOUND, partial run
      2026-07-27.** The planned `ts_recv`/quote-size proxy is superseded: the founder's Alpaca
      condition-code sidecar (design doc §6b) delivers the actual OPRA multi-leg codes, and
      `tools/alpaca_condition_join_probe.py` joins them print-for-print onto the tcbbo tape
      (offset exactly 0ns; join 99.5%). **Measured on 2026-07-24, 8 single names: heuristic
      precision 94.7%, recall 50.9%** — package legs frequently do NOT share one nanosecond, so
      the `multileg=0` arm still contains ~half the true package legs. The heuristic is a floor,
      not a fence. *Remaining:* scale to more names/sessions; any change to the registered
      multileg definition = new addendum (per Addendum D). *Decides (answered):* we do NOT need
      to buy condition-code days — the codes come free from the Alpaca entitlement.
      **CLOSED 2026-07-28:** scale-up happened via the v6 re-roll itself (codes joined corpus-wide,
      Addendum F) and the last gap — legs must be PAIRED, not just flagged, and ns-collision can't
      pair what it can't flag — was measured by `tools/package_leg_gap_probe.py` (5 sessions):
      pairing signal lives at ≤10µs; registered rule = same-underlying single-linkage clusters at
      **W=100µs**, leg = distinct instrument, first-pass o universe = 2-leg equal-size clusters
      ≈53% of package premium ≈30% of all premium (design doc **§3a**). The §3 o probe is UNBLOCKED.
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
Addendum F registers, so the amendment absorbs it in one pass.

⚠️ **The trap that would make this worthless, and the rule that prevents it.**
`P(flow | big move)` is NOT `P(big move | flow)`. If 80% of big rises had heavy bullish flow, that is
worth nothing when 78% of all name-days do. **Every statistic here is computed against a matched
control set — same liquidity tier, same sector, same period, names that did NOT move — and reported
as lift over base rate, never as a raw frequency.** A number without its control is not a finding,
it is a description of the market. No exceptions.

- [x] **1.11 Define the event set — MARKET-RELATIVE, graded RAW.** Founder, 07-27: *"essentially
      measuring alpha over average market returns — not every stock went up."* Correct, and it fixes
      a flaw in the first draft of this file: a top-decile raw move in a rising market selects
      **high-beta names**, so we would "discover" beta. Define the event on the **residual** (move
      net of market/sector beta) in **ATR units**. But **grade the raw move alongside it** — a stop
      does not care about residuals, it is hit by the actual price. Two arms, two different
      questions. Rises and falls stay **separate** throughout; pooling them is exactly what would
      hide a reversal effect.

      **1.11 + 1.12 PRE-REGISTRATION — REGISTERED 2026-07-29 (run at founder direction, "ok
      lets continue", same day). Event set and matched controls registered TOGETHER: the
      control rule needs the event definition, and splitting the registration would let the
      second half be written after the first half's numbers were seen.**

      - **Universe.** Every underlying appearing at least once in `flow_rollup` during
        training, minus the index/ETF fence (`INDEX_ROOTS ∪ ETF_SET`), with split-adjusted
        corpus bars: 6,931 names. Deliberately WIDER than 1.11b's $250k-floored panel — a
        big mover with little or no preceding option flow is exactly the kind of name-day
        the backwards question needs in the denominator ("no flow preceded it" is data,
        not missingness).
      - **Event day, residual arm (PRIMARY).** One-day excess-vs-SPY close-to-close return
        (split-adjusted, SPY session calendar) in units of the name's OWN trailing ATR:
        ATR14 = simple mean of true range ÷ prev close over sessions t−14 … t−1 (≥10
        valid required, else the name-day is ineligible, counted). Event: |z| > 3.0.
        Rises (z > +3) and falls (z < −3) stay separate everywhere downstream. Market
        beta is handled by the excess return; SECTOR beta is deliberately NOT residualized
        (no reliable per-name beta estimates at this breadth) — instead sector membership
        is a MATCHING variable in 1.12, which absorbs the same confound at the control
        level. Plain words: z counts "how many of this name's own typical daily ranges did
        it move, over and above the market" — 3 means a move three times its normal day.
      - **Raw arm (graded alongside, per the founder's stop-loss point).** Same rule on
        the raw return. Both arms recorded; the residual arm is primary for profiling.
      - **Episode collapse (anti-pseudo-replication).** An event day within 5 sessions of
        the previous event day of the same name, sign and arm joins that episode; the
        episode's event date = its FIRST day. A 3-day crash is one episode, not three
        events with near-identical "preceding" windows. Profiling (1.13) uses episode
        dates only.
      - **Threshold calibration disclosure.** 3.0 was chosen after counting candidate
        events on EQUITY BARS ONLY — no flow data was read (script in scratchpad,
        table in BUILD_LOG 2026-07-29). At 3.0, residual arm: 4,278 rise / 3,684 fall
        episodes over 213 training sessions (2,255 / 1,922 distinct names); raw arm:
        2,952 / 3,209 episodes. Enough events to profile, rare enough to mean "big move."
      - **Window.** Training only (events ≤ 2026-05-31); event date needs ≥21 prior bar
        sessions (ATR + tier windows). Early sessions therefore carry no events; counted.
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
- [x] **1.11b Lead, lag, or coincident — run this FIRST, it is an afternoon.** Correlate name-level
      flow against forward and trailing returns at lags −10 … +10 days. The correlation between
      option flow and price is **mechanical and not in question** — options are derivatives. The open
      question is the **arrow of time**: a peak at negative lags means flow leads and there is
      something to trade; a peak at lag 0 or positive means flow is reacting and there is not. Most
      published flow work never separates the three, which is one reason it does not work. *Decides
      where every later step should look.*

      **1.11b PRE-REGISTRATION — REGISTERED 2026-07-29, run at founder direction ("ok start
      1.11b", same day). Discovery look (1c): the whole curve is recorded whatever it shows;
      no pass/fail, no kill authority — but the reading rule is fixed here, in advance.**

      *The question in one sentence:* flow and price definitely move together — the open
      question is who moves FIRST. If today's flow lines up with TOMORROW's price moves, flow
      leads and there is something to chase. If it only lines up with today's or yesterday's,
      flow is following the price, and trading it means buying yesterday's news.

      - **Panel.** Every name-day in the training window (2025-07-28 → 2026-05-31) with
        classified premium ≥ $250k (the registered floor). NO event conditioning — the arrow
        of time is a property of the flow measure itself, not only of alarm days. Primary
        arm = registered tilt with **multileg excluded** (the package fence: v6 measured that
        package legs flip pooled signs, so the blended series would smuggle the fence back
        in). Index/ETF names excluded (`INDEX_ROOTS ∪ ETF_SET`; per 1.22 that fence is a
        floor — the hand-built ETF set is incomplete).
      - **Return series.** Daily excess return vs SPY, close-to-close, split-adjusted corpus
        bars, on SPY's session calendar. For lag k, tilt on session t is paired with the
        ONE-day excess return of session t+k (close of t+k−1 → close of t+k). So k=0 is the
        same-day return, k=+1 the first fully-future day, k=−1 the day before.
      - **Statistic.** Per session, the Spearman rank correlation across names between tilt
        and the lag-k daily excess return (plain words: "do the names with the most bullish
        flow tend to be the names with the best market-relative day k sessions later?" —
        measured on rankings, so one huge mover cannot dominate). Sessions with <30
        qualifying names at a lag are skipped and counted. The reported number per lag is
        the average over sessions, with a 95% interval from resampling whole sessions
        (10,000 draws, one shared resampling across all lags so the curve stays coherent,
        SEED=20260730). Names missing bars at a needed date are skipped per (session, lag)
        and counted, never silently dropped.
      - **Reading rule, fixed in advance.** Report ALL 21 lags −10 … +10 (1.11a's
        whole-curve law; never the peak alone). Lag 0 is expected positive and is
        MECHANICAL — options are derivatives; it is the yardstick, not a finding. "Flow
        leads" may be claimed only if positive lags (k ≥ +1) show correlations that are
        (a) nonzero by the interval AND (b) coherent — same sign at adjacent lags. 21 looks
        at 95% expect ~1 lone false positive, so an isolated one-lag spike is registered
        in advance as noise, not signal. Negative lags nonzero with dead positive lags =
        flow chases price; that too is a finding and gets recorded plainly.
      - **Report-only legs:** (i) ALL arm (multileg included) — shows what the fence
        changes; (ii) SHORT_OTM fenced arm — the academic prior's home (short-dated OTM);
        (iii) $1M floor — the tilt audit measured the axes disagreeing MORE on high-premium
        name-days, so the curve may differ where the money is; (iv) **magnitude lane**
        (founder's direction-free question, 07-29, in its cheapest form): |tilt| vs
        |excess return| at the same lags — does unusual flow foreshadow MOVEMENT, either
        way, even if it cannot call the side?
      - **Disclosure:** positive lags from late-May sessions read June equity CLOSES —
        outcome bars only; test-split flow stays unmined (2.4 precedent). Early sessions
        lack full trailing lags (bars begin 2025-07-25); skipped and counted.

      **1.11b RESULT (2026-07-29, `tools/lead_lag.py`, single run per leg, curves recorded
      in full per the whole-curve law).** Panel: 212 sessions × 3,263 names, 178,624
      name-days (~843/session) on the primary arm.

      - **Flow does NOT lead price at daily grain — anywhere we looked.** Primary arm:
        every positive lag +1 … +10 is a hair from zero with the interval straddling it
        (largest +0.0059 at k=+1, CI [−0.0017, +0.0136]). Same in the ALL arm, the $1M
        floor, and — most pointedly — **SHORT_OTM, the academic prior's home: stone dead
        at every non-zero lag** (all 20 CIs straddle zero) while carrying the STRONGEST
        same-day coupling of any leg (+0.323). The coherence bar for "flow leads" is not
        approached, let alone met.
      - **Lag 0 behaves exactly as registered:** +0.241 fenced / +0.197 blended /
        +0.323 short-OTM / +0.286 at $1M — big, mechanical, no authority. Packages
        DILUTE it (blended < fenced), consistent with the fence.
      - **The one real structure: flow CHASES yesterday, and the chase grows with the
        money.** k=−1 is the only lag with replicated nonzero readings: **+0.0114
        [+0.0039, +0.0190] fenced, +0.0163 [+0.0069, +0.0258] at $1M**, directionally
        present but diluted in the blended arm (+0.0052), absent in SHORT_OTM. Recorded
        honestly: −1 is a single lag, so the adjacent-lag coherence test cannot apply;
        the evidence is its replication across two independent legs. Lone spikes
        elsewhere (−10 in the $1M leg, +7 negative in the blended arm; ~1 false positive
        per 21-lag curve expected at 95%) are pre-registered noise and claimed as
        nothing.
      - **Magnitude lane (leg iv): no time-structure either.** |tilt| vs |move| is a
        FLAT ≈−0.03 at every lag −10 … +10 (all CIs clear of zero, both directions,
        no decay, no asymmetry) with lag 0 sitting ~+0.037 ABOVE that background —
        i.e. the flat negative band is a persistent cross-name TRAIT correlation
        (one-sided-tilt names are systematically smaller-|move| names in this panel),
        not a dynamic signal, and the only dynamic term is the same-day mechanical one.
        One-sided flow does not foreshadow bigger movement at any daily horizon.
      - **What this decides for the later steps (the registration's stated purpose):**
        the unconditional daily-grain prior is now LOW — flow at this grain is
        coincident-plus-chase, not leading. 1.12–1.14 remain the right next steps
        precisely because they ask the CONDITIONAL question this scan cannot answer:
        a rare predictive configuration (extreme events, specific shapes, catalyst
        windows) would dilute to invisibility inside a ~843-name/day pooled rank
        correlation. What dies today is "flow broadly leads price"; what survives is
        only the narrower hypothesis the profiling ladder exists to test — with
        expectations set accordingly. Impact-vs-drift (1.11a-ii) reading: at daily
        grain, everything lives in the impact/coincident term, nothing in drift.

- [x] **1.12 Build the matched control set.** For every mover, controls matched on liquidity tier,
      sector and date. *Done when:* the control set's own base rates are computed and written down
      BEFORE any mover statistic is looked at.

      **1.12 registration (continues the joint 1.11+1.12 block above, same date).**

      - **Matching variables.** (1) DATE: controls share the episode's event session.
        (2) LIQUIDITY: cross-sectional quintile, that session, of trailing-20-session
        median dollar volume (close × volume, ≥15 valid sessions required). (3) SECTOR:
        EDGAR SIC division via `research/fundamentals.py::sic_to_sector` — the same
        source/bucketing the S&P fixture used, fetched 2026-07-29 for the full 6,931-name
        universe into `data/research/fundamentals.db` (coverage disclosed at run time;
        names without a resolvable SIC match on date + quintile only, counted).
      - **"Did not move."** A control candidate must have |z_residual| < 1.0 on the event
        session AND no event day of either sign, either arm, in sessions t−20 … t (an
        event inside the profile window would contaminate the base rate).
      - **Draw.** 5 controls per episode, without replacement within an episode, reuse
        across episodes allowed; deterministic RNG, SEED=20260729. Relaxation ladder when
        a cell has <5 candidates: full match (sector + quintile + date) → quintile + date
        → sector + date → date only; every rung's usage is counted and reported. Episodes
        with zero candidates even at date-only are dropped and counted.
      - **Control base rates (the done-when), computed on CONTROL name-days ONLY — mover
        flow stays unread until 1.13 registers its exact comparisons.** Windows: 5 / 10 /
        20 sessions ending at t−1 (the event day itself is excluded — its flow is the
        mechanical lag-0 term 1.11b measured, not "preceding" anything). Flow basis: the
        registered fenced tilt (classified premium, multileg excluded), except the
        multileg-share feature which by definition uses the blended total. The battery —
        the day-grain forms of every 1.13 hypothesis:
        1. **active days**: count of window days with classified premium ≥ $250k;
        2. **max same-sign run**: longest streak of same-sign daily tilt among active days
           (the "multiple days of bull flow" hypothesis's yardstick);
        3. **window tilt**: Σ(bull−bear)/Σ(bull+bear) over the window;
        4. **largest-print ratio**: max `premium_max` in the window ÷ the name's own
           trailing-60-session median daily `premium_max` (≥20 valid days, else NA,
           counted) — the "abnormally large sweep vs own normal" yardstick, day grain;
        5. **concentration**: top day's share of window classified premium (active
           windows only);
        6. **mix shares**: call share, short-DTE share (0-7 ∪ 8-30), OTM share
           (otm2_10 ∪ otm10p) of classified premium; multileg share of TOTAL premium.
        Reported as p10/p25/p50/p75/p90/p99 over control windows per window length,
        pooled across signs (controls didn't move; the episode-sign mapping is stored for
        1.13). Recorded in this file + BUILD_LOG before any mover statistic exists.
      - **Output.** `tools/mover_controls.py` → `data/research/mover_controls.json`
        (episodes, matched controls, base rates) — the frozen input 1.13 profiles
        against; regenerates deterministically from the corpus.

      **1.11+1.12 RESULT (2026-07-29, `tools/mover_controls.py`, single run; mover flow
      never read).** Event set: residual arm **4,204 rise / 3,534 fall** episodes, raw arm
      2,818 / 3,171 (slightly under the calibration table's counts — the registered
      ≥21-prior-sessions rule removes the August-2025 edge, as disclosed). Controls:
      **68,635 draws = 13,727 episodes × 5, zero episodes dropped**; ladder use 39,781
      full match / 28,744 quintile+date / 10 sector+date / 100 date-only. Sector coverage
      3,866 / 6,931 names (EDGAR SIC; 5,745 episodes in no-sector names — largely ETFs
      past the hand-built fence and digit-suffixed adjusted option roots — start at the
      quintile rung per the registered fallback, and their 5,745×5 ≈ the quintile-rung
      count: with-sector episodes filled at full match almost every time). 59,958 unique
      control name-days carry the base rates.

      **Control base rates (the done-when), recorded before any mover statistic exists.**
      Quantiles p10/p25/p50/p75/p90/p99 per window in the run log + JSON; what they say:
      - **The MEDIAN control window has ZERO active days** (days ≥ $250k classified
        premium): p50 = 0 at 5d, 10d and 20d, while p90 ≈ the full window (5/9/18) —
        strongly bimodal: a minority of quiet names carry continuous flow, most carry
        none. Plain words: most names that did NOT move had no notable options flow at
        all — so mere PRESENCE of flow is the first thing 1.13 must beat, and lift
        claims must survive the liquidity-tier match (presence correlates with size).
      - **Same-sign runs are genuinely rare among controls**: max run p90 = 2/3/4 and
        p99 = 4/6/8 at 5/10/20d. A 4-day same-sign streak inside a 10-day window is a
        ~1-in-100 control event — the founder's "multiple days of bull flow" hypothesis
        now has a real yardstick instead of a guess.
      - **Window tilt is symmetric and centered on zero** (p50 ≈ −0.01 everywhere, wide
        spread) — no ambient bullish/bearish skew to correct for.
      - Yardsticks for the rest of the battery: largest-print ratio p50 ≈ 3.3/5.7/9.7×
        (rises with window length mechanically — max-of-window vs 60d median), p99 ≈
        111/161/249×; call share p50 ≈ 0.80; short-DTE share p50 ≈ 0.20–0.22; OTM share
        p50 ≈ 0.36–0.39; multileg share p50 ≈ 0.10–0.15.
      - NA columns are themselves base rates and stay recorded: e.g. 12,077 / 59,958
        5d-windows have no classified premium at all (window_tilt NA), 46,144 have no
        active day (concentration NA).
- [x] **1.13 Profile the preceding flow**, 5 / 10 / 20 days before each event, movers vs controls:
      consecutive same-sign tilt days (*the "multiple days of bull flow" hypothesis*); largest single
      print relative to that name's own normal, split by liquidity tier (*the "abnormally large
      sweeps on smaller caps" hypothesis*); concentration — one day or spread out; DTE mix; moneyness
      mix; call/put mix; multi-leg share. **Candidate feature from 1.11b (founder question
      2026-07-29, "can we use the chase for positioning?"):** chase-adjusted tilt — residualize
      each name-day's tilt on the prior day's excess return (1.11b measured the chase as the
      NORMAL behavior of uninformed flow, k=−1 the only replicated structure), so the profile
      asks about UNEXPLAINED flow; heavy flow after a move is what the crowd always does, heavy
      flow with no prior move is the anomaly worth profiling. Exact form registered with 1.13,
      not here.

      **1.13 PRE-REGISTRATION — REGISTERED 2026-07-29, run at founder direction ("go",
      same day). The first look at mover flow. Discovery (1c): every number reported,
      no pass/fail; the reading rule is fixed here before the run.**

      - **Inputs, all frozen by 1.12:** the episode set, the 5-per-episode matched
        controls, the window features exactly as `tools/mover_controls.py` computes them
        (same code paths), and the pooled control quantiles in
        `data/research/mover_controls.json`. Windows 5/10/20 ending t−1. Residual arm =
        primary; raw arm = replication leg. Rises and falls SEPARATE everywhere.
      - **Primary statistic per (feature, window, sign): exceedance-share DIFFERENCE.**
        Share of mover windows at or beyond a frozen control threshold, minus the same
        share among their matched controls. Thresholds: the FROZEN pooled control p90
        (upper tail) and p10 (lower tail) from 1.12 — both tails reported for every
        feature, no picking; p99/p1 reported as secondary color. Shares are computed
        over non-NA windows, and each feature's NA share (mover vs control) is itself
        reported — "had no classified flow at all" is a finding, not a dropout.
        The ratio (mover share ÷ control share) is reported as the readable lift, but
        the DIFFERENCE carries the interval — ratios explode when the denominator is
        near zero.
      - **Presence comparison** (the base-rate headline made a test): share of windows
        with ≥1 active day, mover vs matched control, same difference-with-CI form.
      - **Uncertainty.** 95% CI on each difference by bootstrap over EVENT SESSIONS
        (episodes cluster on market-wide vol days; resampling sessions keeps that
        dependence), 10,000 draws, one shared resampling across all features/windows per
        draw, SEED=20260731.
      - **Chase-adjusted tilt (the 1.11b follow-on, exact form).** Daily tilt on active
        days regressed (OLS, single pooled slope b) on the PREVIOUS session's
        excess-vs-SPY return in own-ATR14 units, estimated on the 59,958 unique CONTROL
        name-days only (untainted by movers; b recorded). Feature: window_tilt minus
        b × (classified-premium-weighted mean of prev-day excess z over the window's
        flow days; available-x weighting, NA if no finite x). Thresholds for this ONE
        feature cannot come from the 1.12 freeze (it did not exist): its control
        p10/p90 are computed on the same unique control name-days at run start, before
        any mover value — enforced by code order, disclosed here.
      - **Liquidity split (the "small caps" hypothesis):** largest-print-ratio and
        presence differences additionally reported per liquidity quintile (matching
        makes the split clean); thin cells counted, never silently dropped.
      - **Reading rule, fixed in advance.** ~11 features × 3 windows × 2 tails × 2 signs
        ≈ 130 primary-arm intervals ⇒ **expect ~6–7 false positives at 95% by chance
        alone.** A profile difference is a FINDING only if: (a) its CI is clear of zero,
        (b) it repeats across ≥2 window lengths, (c) it replicates in direction in the
        raw arm, and (d) it survives the liquidity split (not a size artifact). Anything
        weaker is reported as noise-compatible. Signed hypotheses (bullish-before-fall
        reversal etc.) belong to 1.14, not here — 1.13 asks WHETHER movers' preceding
        flow differs, feature by feature.
      - **Disclosure.** Mover flow is read for the first time under this registration.
        All windows end at t−1 ≤ 2026-05-28: training flow only, test split untouched.
        Output: full tables + `data/research/mover_profile_113.json`
        (`tools/mover_profile.py`).

      **1.13 RESULT (2026-07-29, `tools/mover_profile.py`, single run; full tables in the
      JSON — this block is the reading-rule verdict on them).** Chase slope b = +0.00358
      (9,348 active control name-days). ~60 starred intervals appeared against ~6–7
      expected false — but the reading rule collapses most into ONE confound family
      before anything is claimed:

      - **CONFOUND, not finding: the presence deficit.** Movers have LESS preceding flow
        than controls (presence −4.7…−10.7pp residual, growing with window; every
        NA-share star is the same fact). The liquidity split localizes it in the top
        quintiles (q4 20d: mover 55% vs control 87%) and it FLIPS SIGN in the raw-rise
        arm — a within-quintile name-trait artifact (movers sit at the less-optioned end
        of coarse dollar-volume buckets), failing registered checks (c) and (d). The
        max_run deficit tracks it mechanically and also flips in raw rises ⇒ **the
        "multiple days of same-sign flow" hypothesis gets NO day-grain support.** The
        otm_share-lo and multileg-lo elevations ride the same activity confound (both
        flip in raw rises); recorded, not claimed.
      - **FINDING 1 — the flow that DOES precede movers is SHORT-DATED.** Top-decile
        short-DTE share (conditional on having flow, so the presence confound points the
        other way): falls +9.1/+7.4/+3.8pp over ~10% base at 5/10/20d (≈1.4–1.9×), rises
        +4.1/+3.5/+2.8pp; raw arm replicates (rise 20d borderline); the zero-short-DTE
        tail is flat, so it is a tenor shift, not thin-window extremization.
      - **FINDING 2 — before FALLS, preceding flow arrives as a single-day burst.**
        Concentration-hi +4.9/+3.5pp (20d +2.3 ns) with the spread-out tail suppressed
        −4.7/−5.4/−3.4pp, all raw-replicated. Rises: absent.
      - **FINDING 3 — before FALLS, an abnormally large single print (vs the name's own
        60d normal) is ≈1.2–1.4× more common:** +4.4pp at 5d (14.6% vs 10.2%), positive
        at 10/20d, raw-replicated (20d ns), and the split shows it in EVERY size tier
        (q1–q4 positive, most starred) — "abnormally large sweeps," yes; small-caps-
        specific, no. Findings 2+3 are plausibly one phenomenon: bursty, outsized flow
        shortly before big falls. The asymmetry itself (falls ≫ rises) matches the
        academic prior that informed options trading concentrates on the negative side.
      - **1.14 pointer, NOT a finding:** BULLISH-tilted flow modestly elevated before
        falls (window_tilt-hi +2.1/+1.9/+2.0pp, residual, all windows) — the
        Pan–Poteshman reversal flavor — but it fails raw-arm replication (c), so it goes
        to 1.14 as the thing its sign test exists to check. Bearish-tilt-before-falls:
        nothing, anywhere.
      - **Chase-adjusted tilt: INERT.** b is tiny (as 1.11b's +0.011 rank correlation
        implied); adjusted and raw tilt rows are near-identical everywhere. The founder's
        residualization idea was worth building and is now measured: at day grain the
        chase is too weak to relabel any window. Recorded, retired at this grain.
      - **Standing caveat:** shares are conditional on non-NA windows; unconditional
        lift = presence × conditional. And day-grain windows cannot distinguish
        "informed flow before the event" from "flow reacting to pre-event drift" —
        that separation belongs to 1.13b (volume expansion) and finer grains.
- [x] **1.13b Equity activity AFTER the flow.** Founder, 07-27. Distinct from the Phase 3 hedge
      footprint — that is seconds, this is days. Did underlying **volume expand** in the sessions
      following unusual option flow, and does flow-plus-volume-expansion foreshadow better than flow
      alone? Free: daily volume is already in `equity_bars.v`. A two-part pattern is also harder to
      fake than a one-part one, so if it holds it is more likely real.

      **1.13b PRE-REGISTRATION — REGISTERED 2026-07-29, run at founder direction ("go ahead
      on 1.13b", same day). Stays in the backwards frame (1c law): equity-volume features
      join the preceding-window profile, movers vs frozen matched controls. Third read of
      mover-window data; first read of mover equity volume as a profile feature (volume
      already served as a matching variable — see the conservative-bias disclosure).**

      - **Inputs, frozen by 1.12:** episode set, matched controls, windows 5/10/20 ending
        t−1, residual primary / raw replication, training only. Dollar volume dv = close ×
        share volume from the same bars matrices the matching quintiles used.
      - **Baseline B** per (name, t, W): median dv over the 60 sessions ending at t−W−1
        (≥20 finite required, else NA) — strictly PRE-window, so expansion inside the
        window cannot leak into its own yardstick.
      - **Window features (same code paths for movers and controls):**
        1. `vol_expansion` = mean window dv ÷ B.
        2. `vol_max_ratio` = max window dv ÷ B (the volume analog of the largest-print
           ratio).
        3. `post_flow_vol` = mean dv over days STRICTLY AFTER the window's largest flow
           day (argmax classified premium among active ≥$250k days) ÷ B; NA if no active
           day or the largest flow day is the window's last. The founder's literal
           question: volume in the sessions following the unusual flow, still before the
           move.
        4. `flow_leads_vol` = indicator that the largest flow day strictly precedes the
           window's max-volume day; NA if no active day. Ordering evidence for
           informed-flow-before vs reaction-to-drift — evidence, not proof; finer grains
           remain the real separator.
        5. **Two-part battery:** `flow_sig` = largest_print_ratio ≥ the FROZEN 1.12
           control p90 (the 1.13 finding's signature); `vol_hi` = vol_expansion ≥ control
           p90. D reported for `two_part` (flow_sig AND vol_hi), `flow_only` (flow_sig
           AND NOT vol_hi), `vol_only` (vol_hi AND NOT flow_sig).
      - **Statistics:** exceedance-share differences exactly as 1.13 — features 1–3 at
        control p90/p10 (both tails — volume CONTRACTION before movers would be a finding
        too), feature 4 and the two-part battery as single shares; NA shares reported.
        New features' thresholds cannot come from the 1.12 freeze: computed on control
        windows FIRST at run start, before any mover value (code-order enforcement, as
        1.13's chase feature). Session-cluster bootstrap, 10,000 draws, one shared
        resampling, SEED=20260803. Battery: 10 stats × 3 windows × 2 signs = **60
        residual intervals ⇒ ~3 expected false positives.**
      - **Reading rule:** as 1.13 — (a) CI clear of zero, (b) ≥2 windows, (c) raw-arm
        direction replication, (d) liquidity split survival (split reported for
        vol_expansion + two_part). **"Two-part foreshadows better" is supported only if**
        D(two_part) is a finding under (a)–(d) AND the two_part mover÷control ratio
        exceeds the flow_only ratio at the same windows.
      - **Conservative-bias disclosure (registered before the run, not discovered
        after):** controls are matched on the trailing-20d median dv over t−20…t−1, which
        OVERLAPS the feature windows — fully at 20d, least at 5d (a 20-day median barely
        moves for a ≤5-day spike). Genuine mover volume expansion is therefore partially
        matched away: the test is biased TOWARD null, most at 20d. Findings that survive
        are stronger for it; **a null at 20d is NOT evidence of absence.**
      - **Disclosure.** Windows end t−1 ≤ 2026-05-28, training only, test split
        untouched. Output: full tables + `data/research/mover_volume_113b.json`
        (`tools/mover_volume.py`).

      **1.13b RESULT (2026-07-29, `tools/mover_volume.py`, single run; full tables in
      the JSON — this block is the reading-rule verdict).** Far more coherent structure
      than the ~3 expected false positives; the verdicts, feature by feature:

      - **FINDING 1 — the strongest row in the study so far: a volume-SPIKE day sits in
        the pre-move window.** vol_max_ratio-hi (max window volume ≥ ~4–7× own pre-window
        normal) elevated at ALL windows, BOTH signs, ALL raw-replicated — 12/12 starred:
        rises +5.4/+4.8/+4.3pp, falls +6.9/+5.1/+5.1pp over ~10% base (≈1.4–1.7×), with
        the quiet mirror (lo tail) suppressed in 5 of 6 residual cells. Unlike every flow
        finding it is symmetric across move direction. (Split registered only for
        vol_expansion + two_part; given Finding 2's localization the spike likely shares
        some of it — un-split by registration, stated not hidden.)
      - **FINDING 2 — sustained volume expansion before moves is a THIN-NAME
        phenomenon.** vol_expansion-hi clears the rule (rises 2 windows, falls 3, raw
        direction replicates) but the split localizes it: q0/q1 carry +5…+13pp while
        q2–q4 sit at zero (q4 rise 20d even flips to −3.2pp*). In liquid names the
        pre-move window does NOT run abnormally loud overall — the signal there is the
        single spike day (Finding 1), not a louder tape.
      - **FINDING 3 — the founder's literal question: volume DOES expand in the sessions
        after the flow day, at short range.** post_flow_vol-hi: rises +7.6pp (5d) and
        +4.0pp (10d) both CI-clear with raw 5d replication; falls +7.3pp at 5d (10d
        +3.4pp, CI grazing zero); gone by 20d. Scope caveat: defined only on windows with
        an active flow day before the window's last session (~9–13% of mover windows —
        the 1.13 presence confound selects this subset; its NA rows star for exactly that
        reason, recorded not re-claimed).
      - **NOT SUPPORTED — "flow + volume expansion foreshadows better than flow alone."**
        two_part stars once (fall 5d +0.9pp on a ~2% base) ⇒ fails ≥2 windows; the
        registered better-than-flow_only gate is therefore not reached. flow_only (the
        abnormal print WITHOUT volume expansion) carries the fall-side discrimination on
        its own (+3.4pp* at 5d residual; raw +4.5/+2.3pp*) — the 1.13 print signature
        does not need volume confirmation, the joint cell is rare (power), and the two
        parts overlap in information. The two-part idea stays alive only as a finer-grain
        question.
      - **Ordering (flow_leads_vol): NO signal.** Movers' biggest flow day does not
        precede their volume peak more often than controls' (residual ns everywhere).
        The informed-flow-before vs reaction-to-drift question stays unresolved at day
        grain, exactly as the standing caveat anticipated. Raw-rise-only stars
        (−4.2/−4.3pp) pattern-match the raw-rise confound family; recorded, not claimed.
      - **Confound bookkeeping:** post_flow_vol / flow_leads_vol / two_part NA elevations
        (+3…+17pp) are the 1.13 presence/optionability confound recurring through
        feature definitions; vol_expansion NA +1.1–1.6pp is thin bar history. Recorded.
      - **Standing conservative bias (registered pre-run):** volume-quintile matching
        overlaps the windows, so all expansion effects here are floors, and the 20d
        fades are partly mechanical.
- [x] **1.14 Test the sign directly.** Compute the profile before big **rises** and before big
      **falls** separately. Same profile ⇒ no signal. Bullish flow preceding falls more often ⇒ the
      reversal effect, which is what Pan & Poteshman measured (price pressure at d+1, significantly
      reversing d+2 through d+10 — our exact window). If it replicates here it **inverts the sign the
      entire retail industry sells**, which would be the most valuable single finding available to us.
      *Note:* partially absorbs measurement 1.10's cross-tab — run 1.10 inside this frame.

      **1.14 PRE-REGISTRATION — REGISTERED 2026-07-29, run at founder direction ("go", same
      day). The targeted sign test: does the DIRECTION of preceding tilt carry information
      about the direction of the coming move — right-way, reversed, or none. Second read of
      mover flow, same frozen windows.**

      - **Inputs, all frozen by 1.12:** episode set, 5-per-episode matched controls, window
        machinery (`tools/mover_controls.py` code paths), windows 5/10/20 ending t−1,
        residual arm primary / raw arm replication, training only (t−1 ≤ 2026-05-28).
      - **Direction feature 1 — all-flow tilt sign.** A non-NA window is BULLISH if
        `window_tilt` (frozen definition) > 0, BEARISH if < 0; tilt exactly 0 counted in
        neither share (count disclosed). Parameter-free — no threshold to choose.
      - **Direction feature 2 — short-DTE tilt sign.** Same tilt form restricted to
        `dte_bucket IN ('0-7','8-30')` on the identical fence (side buy/sell, multileg=0):
        bull_short = short-DTE call-buy + put-sell premium; tilt_short =
        (2·bull_short − cls_short).sum ÷ cls_short.sum over the window; NA if the window
        has zero short-DTE classified premium. Registered reason: 1.13 localized the
        informative flow in short tenor, so the sign question is tested where the signal
        lives. Read independently of feature 1; if the two star in OPPOSITE directions the
        result is recorded as UNRESOLVED, never cherry-picked.
      - **Primary statistic per (feature, window):** D_sign = bull-share(movers) −
        bull-share(matched controls), computed for rises and falls separately, then the
        contrast **C = D_fall − D_rise**. Controls cancel name/session/sector/size
        composition within each sign; C compares the excesses. Sign map fixed in advance:
        **C > 0 ⇒ reversal** (excess bullishness before falls — the Pan–Poteshman
        direction); **C < 0 ⇒ right-way flow**; CI covering 0 ⇒ no day-grain sign
        information in tilt. Bull/bear shares both tabulated but the interval sits on the
        bull-share D and on C only (bear ≈ mirror; no double counting). Shares over non-NA
        windows; NA shares reported but the presence confound is 1.13's recorded fact, not
        re-litigated here.
      - **Uncertainty.** Same session-cluster bootstrap: 10,000 draws over event sessions,
        one shared resampling across all statistics, SEED=20260802; C per draw = that
        draw's D_fall − D_rise. Battery: 2 features × 3 windows × {D_rise, D_fall, C}
        residual = **18 primary intervals ⇒ ~1 false positive expected at 95%.**
      - **Secondary / color (no stars claimed):** (i) magnitude version — mean window
        tilt, mover minus control, per sign + contrast; (ii) continuity rows — the frozen
        p90/p10 tilt-tail exceedance rows from 1.13 (the pointer that sent us here)
        re-reported per sign with the contrast; (iii) descriptive cross-tab — episode
        counts by (tilt sign × move sign) per window, residual arm, control shares
        alongside. The cross-tab covers the flow-sign×move-sign quadrant of 1.10;
        1.10 (d)'s drift×sweep 2×2 with production sweep parameters stays open.
      - **Reading rule.** A sign FINDING requires (a) C's CI clear of zero, (b) ≥2 window
        lengths, (c) raw-arm direction replication, (d) survival of the liquidity split
        (D and C per quintile for feature 1, same bootstrap, robustness not primary;
        unknown-quintile episodes counted, never silent).
      - **Outcome map, registered so the trading meaning is not improvised after the
        look:** right-way ⇒ tilt enters perception as directional evidence; reversal ⇒
        tilt enters with INVERTED sign (the industry-inverting outcome); none ⇒ tilt is
        retired as a direction carrier at day grain and direction must come from other
        angles — 1.13's direction-free shape findings stand regardless.
      - **Disclosure.** Output: full tables + `data/research/mover_sign_114.json`
        (`tools/mover_sign.py`). Test split untouched.

      **1.14 RESULT (2026-07-29, `tools/mover_sign.py`, single run; full tables in the
      JSON). The registered answer: NO SIGN. Every primary contrast C = D_fall − D_rise
      straddles zero — both features (all-flow tilt, short-DTE tilt), all three windows,
      residual arm: 0 of 6 C intervals clear of zero against ~1 expected false positive.
      (The lone raw-arm C star, short-DTE 5d −0.036, is single-window, sign-flips by 20d,
      and sits in the replication arm — pre-registered noise.) Per the registered outcome
      map: tilt is RETIRED as a direction carrier at day grain — neither right-way flow
      nor the Pan–Poteshman reversal.**

      - **What the tables show instead of a sign: a small direction-FREE bullish excess.**
        All 12 residual D point estimates are positive — movers' preceding flow leans
        ~1–4pp more bull-tilted than its matched controls before rises AND before falls
        alike. Starred only at 5d (fails the ≥2-window rule ⇒ noise-compatible even as a
        level effect) and symmetric across move direction ⇒ zero contrast. The cross-tab
        says it plainly: signed mover windows are ~51% bullish before rises and ~50%
        before falls, each ≈2pp above their own controls.
      - **The 1.13 pointer resolves as this symmetry, not a reversal.** The frozen-tail
        continuity rows reproduce 1.13's bullish-tail-before-falls elevation (+~2pp,
        residual, all windows), but the contrast vs rises straddles zero everywhere in
        the residual arm — bullish-tilt-before-falls was the fall-side half of a
        both-signs bullishness excess, not sign information.
      - **Raw-arm color, recorded not claimed:** before raw RISES both extreme-tilt
        tails are suppressed (tilt-hi −2.7pp, tilt-lo −3.0pp), a less-extreme-tilt
        effect that makes the raw tail contrasts star; absent in the residual arm and
        pattern-matching 1.13's raw-rise confound family.
      - **Liquidity split:** no quintile shows a CI-clear contrast in either direction —
        the null sign verdict is not hiding a size pocket. (8 rise / 5 fall
        unknown-quintile episodes excluded, counted.)
      - **Consequence, per the registered outcome map:** the agent's day-grain flow
        perception is SHAPE, not direction — 1.13's short-dated / bursty /
        outsized-print profile flags "big move risk, especially downside," and direction
        must come from other angles (trend, news, catalyst context). Direction may still
        live at finer grains or inside catalyst subsets (1.14b), but the day-grain sign
        question is answered and closed.
- [ ] **1.14b Split events by catalyst vs no-catalyst** (founder framing, 07-27: correlation breaks
      at catalyst moments — earnings, guidance, M&A, FDA). "Someone knew about the event" and
      "someone moved the stock" are different stories with plausibly different preceding-flow
      profiles; pooling them repeats the index/single-name mistake one level down. Earnings/opex
      flags already exist as covariates — this makes the split a registered look in the ledger.

      **1.14b PRE-REGISTRATION — REGISTERED 2026-07-29, run at founder direction ("go
      ahead on 1.14b", same day). Does the established pre-move fingerprint differ
      between catalyst and no-catalyst events? Registered BEFORE any per-episode
      catalyst flag was computed (the earnings-date fetch is pure acquisition; the
      flag definition below is fixed here).**

      - **Catalyst flag:** an episode is EARNINGS-ADJACENT if ≥1 EDGAR 8-K Item-2.02
        filing date for the name falls within ±1 SPY session of the episode date.
        The ±1 window is motivated by the recorded EDGAR filing-hour bias (a filing
        can lag the announcement by a session) — registered, not tuned. Data:
        `data/research/earnings.db` built by the existing `research/earnings.py`
        for the mover names only (controls need no flag — they were required quiet).
        Names with no CIK / no dates cannot be flagged: counted, reported, and left
        in no-catalyst with the contamination disclosed.
      - **Honest scope, stated up front:** catalyst detection = EARNINGS ONLY. M&A /
        FDA / guidance / analyst days are not detectable with free keyless data, so
        "no-catalyst" is really "no-KNOWN-catalyst" and the contamination pushes the
        two subsets TOWARD each other ⇒ a no-difference result is weak evidence; a
        difference that survives it is strong evidence.
      - **Features — the four established fingerprint components, zero new degrees of
        freedom:** short_dte_share-hi, concentration-hi, largest_print_ratio-hi (all
        at the FROZEN 1.12 control p90s) + vol_max_ratio-hi (1.13b's controls-first
        thresholds, recorded in `mover_volume_113b.json` meta, reused verbatim).
      - **Statistics:** per (feature, window, sign): D_cat = mover−control
        exceedance-share difference among catalyst episodes, D_nocat = same among
        no-catalyst episodes, primary contrast **X = D_cat − D_nocat** (paired per
        draw, as 1.14's C). Residual primary / raw replication. Battery: 4 features
        × 3 windows × 2 signs = **24 primary X intervals ⇒ ~1 expected false
        positive.** D components reported with CIs; X carries the verdict. Also
        reported descriptively: the catalyst share of episodes per sign/arm, and the
        presence comparison per subset.
      - **Uncertainty:** session-cluster bootstrap, 10,000 draws, one shared
        resampling, SEED=20260804.
      - **Reading rule:** a split FINDING requires (a) X's CI clear of zero, (b) ≥2
        window lengths, (c) raw-arm direction replication. **Interpretation map,
        fixed in advance:** fingerprint concentrated in the catalyst subset ⇒
        positioning before scheduled news — perception watches event-adjacent
        windows (feeds the event-gated idea flywheel); concentrated in no-catalyst ⇒
        non-public-information-flavored, the more alarming and more valuable alert;
        no difference ⇒ general mechanism, remembering the contamination bias makes
        this the weakest conclusion.
      - **Disclosure.** Fourth read of mover windows; features and thresholds all
        pre-existing. Training only, test split untouched. Output: full tables +
        `data/research/mover_catalyst_114b.json` (`tools/mover_catalyst.py`).

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
found here becomes a registered hypothesis in Addendum F before it may be tested there.

### 1d. The prereg amendment (Addendum F — E was taken 07-27 by the multileg redefinition)

- [ ] **1.15 Correct baseline #4** to production's real parameters. It currently says "$500k
      premium, DTE≤30", which describes nothing we run. "We beat production" would be a false claim.
- [ ] **1.16 Make the kill rule able to fail.** Today: dead only if it fails at **all** four horizons
      across **all** five quintiles, over ~64 declared arm variants. Something always survives by
      luck. *Fix:* declare **one primary cell** (recommend: midpoint sign, multileg-excluded,
      pooled quintiles, terminal alpha vs SPY in ATR units). Alive only if that passes. Everything
      else is exploratory, labelled, with no survival authority.
      ⚠️ **The "pooled quintiles" half of that recommendation is superseded by 1.23** (A2, 07-29):
      pooling mixes tiers whose noise floors differ 4×. The primary cell must name a quintile.
- [ ] **1.17 Horizon of record = 10 trading days.** 12 months gives 2–4 independent observations at
      21/45 days. Report them; state in advance they cannot confirm or kill anything.
- [ ] **1.18 Fix the significance bar.** Naive √N assumes 5,554 independent names; the real figure is
      **6.4** (verified). Use time-block-clustered standard errors and require ≥20 independent event
      dates per cell.
- [ ] **1.19 State universe size on every table.** The top ~1,000 names carry 99.6% of premium.
- [ ] **1.20 Split the abstention band's reporting into single-name vs index/ETF arms** (founder,
      07-27). The registered 20–35% band (Addenda C/D) is measured on POOLED premium, which is
      index-dominated (~31% of premium is index roots alone; `t`-coded SPX floor packages were
      10.35% of one session) — so the pooled band largely reports SPX's behavior with single names
      along for the ride. Reporting both arms alongside the pooled number is legal now; making the
      band BINDING per-arm is a rule change and lands here.
- [ ] **1.21 Standing rule: every pooled premium-weighted statistic reports its single-name arm
      alongside** (founder, 07-27). Measured justification: single names net −$354M while
      index/ETF net +$908M on the same session — pooled statistics cancel opposing signals and
      read as mush. This generalizes 1.8's lesson from multi-leg to everything.
- [ ] **1.22 Harden the ETF classification before 1.20/1.21 lean on it.** `ETF_SET` in
      `tools/multileg_arm_probe.py` is hand-built and incomplete (its own header says so). Derive
      or curate a proper equity-vs-ETF-vs-index classification once, register it, reuse everywhere.
- [ ] **1.23 The noise floor is a CURVE — grade per liquidity tier, and divide rather than
      subtract.** Consequence of 1.7's A2 (07-29): the exact null fires at **2.129% / 3.386% /
      6.282% / 8.810% / 8.927%** across Q1→Q5, pooled 7.362%. Every earlier "≈8.4% regardless of
      liquidity" was the normal approximation and is dead. Four changes, all of which land in
      Addendum F together:
      - **(a) No pooled grading with kill authority.** A pooled hit rate averages tiers whose
        firings are contaminated at 2% and at 9% — different populations. Pooled numbers may be
        *reported*; they cannot confirm or kill. Amends 1.16's primary cell (above).
        ⚠️ **Direction of the old bias, stated so it is not repeated:** a flat 7.4% floor
        over-penalizes thin names by ~3.5× and under-penalizes liquid ones, so it tilts the study
        toward concluding "the signal lives in liquid names" — the error the product can least
        afford, since unusual activity on a thin name is the case it exists to catch.
      - **(b) Dilution correction, per tier, not subtraction.** With observed firing rate `f` and
        that tier's null rate `f₀`, the noise share of firings is `q = f₀/f`, and observed lift is
        diluted by exactly `(1 − q)`: `lift_real = lift_observed / (1 − f₀/f)`. Registered
        assumption, stated because it is load-bearing and unverified: **noise-fired name-days are
        assumed to behave like the 1.12 matched controls.** Where `f ≤ f₀`, the tier reports **"no
        measurable signal"** — never a small positive number, never a pooled rescue.
      - **(c) The base-rate table must be measured on the GRADING window, under the SAME quintile
        definition.** A2's 40-session numbers are a proof of method, not the table: they cannot be
        applied to a 251-session grade, and quintile cut-points computed in one universe do not
        transfer to another. Needs the exact shuffle over the full grading window — **the same
        ≥120-session run 1.7 needs to close**, so one run serves both. Ties to 1.19: the table
        states its universe size per tier or it is not usable.
      - **(d) 1.18's power bar applies PER TIER.** ≥20 independent event dates is now a per-quintile
        requirement. Q1/Q2 will fail it on short windows (A2: SHORT_OTM Q1 = 60 name-days over 40
        sessions). A tier that fails it is declared **unevaluable in advance** and reported as such
        — it is not merged upward into a tier that passes.
      **Deliberately NOT doing this now:** re-thresholding per tier so the null rate is flat (e.g. a
      per-quintile `z` cut targeting 5%). It would make firings comparable across tiers, but it
      changes the registered rule, invalidates everything measured to date, and tunes a parameter
      against an outcome we have not graded — the same call the founder made on 07-28. Revisit
      **after** the first grade, not before.
- [ ] **1.24 Disambiguate the SHORT_OTM arm — FOUNDER CALL 2026-07-29: out-of-the-money only.**
      The prereg defines the arm as "DTE ≤ 30 and **|K/S − 1| ≥ 2%**"
      (`FLOW_BASELINE_PREREG_2026-07-26.md:76-79`). That is a *distance-from-spot* test with no
      direction, so read literally it admits contracts **2% or more IN the money** — close to the
      opposite bet from the one the arm exists to isolate, and in conflict with both the arm's own
      name (SHORT_**OTM**) and the academic prior the same paragraph cites. **Resolution: OTM
      only** (`mny_bucket ∈ {otm2_10, otm10p}`), which is what A1/A2/Addendum B already measured,
      so nothing needs re-running. Registered here as an amendment rather than edited into the
      prereg text; `tools/flow_tilt.py` carries the same note at the implementation. *The ambiguity
      was found while building the tilt, not by the audit.*
- [ ] **1.25 The three registered-but-unbuilt clauses (audit F4) — FOUNDER CALL 2026-07-29:
      MEASURE FIRST, then decide.** Registered and absent from `tools/flow_corpus_rollup.py`:
      (1) drop prints with `spread > 50% of mid` (prereg:60); (2) drop a name-day dropping `>20%`
      of its prints (prereg:61); (3) report every tilt net of the whole-market daily mean
      (Addendum A). **(1) and (2) live at the INGEST layer** — building them re-rolls the full
      year and discards Addendum B's base-rate table, which is too expensive to spend on a guess.
      **(3) is a statistic-level transform needing no re-roll**, so it is not what the decision is
      gated on and can be applied or retired independently.
      - *Decision rule, registered before the measurement:* the question is **not** how many
        prints get dropped but **how far the registered tilt MOVES** when they are. A clause
        removing 3% of prints that never changes a name-day's sign is cosmetic; one removing 0.5%
        that flips signs is load-bearing. `tools/prereg_clause_impact.py` measures |Δtilt| and the
        sign-flip rate on 6 sessions spanning the year and both sides of the 2026-06-26 sentinel
        break. Until it reports, **prereg compliance may not be claimed for these three clauses.**

      **1.25 RESULT — RAN 2026-07-29.** 6 sessions, ~60M prints, 50s. The two ingest clauses split
      in **opposite** directions, so they need separate dispositions.
      | session | wide prints | of classified | wide **premium** | median \|Δtilt\| | p95 \|Δtilt\| | >0.05 | **sign flips** |
      |---|---|---|---|---|---|---|---|
      | 2025-08-15 | 444,735 | 5.74% | 0.317% | 0.0035 | 0.079 | 7.09% | **2.21%** |
      | 2025-11-14 | 489,666 | 5.78% | 0.416% | 0.0063 | 0.133 | 12.59% | **3.96%** |
      | 2026-02-13 | 582,930 | 7.11% | 0.352% | 0.0052 | 0.101 | 10.94% | **2.64%** |
      | 2026-05-15 | 764,939 | 7.61% | 0.429% | 0.0069 | 0.114 | 10.65% | **3.37%** |
      | 2026-06-30 | 522,417 | 5.86% | 0.335% | 0.0043 | 0.088 | 9.21% | **2.89%** |
      | 2026-07-23 | 595,928 | 6.15% | 0.328% | 0.0047 | 0.130 | 9.25% | **3.05%** |
      - ⛔ **Clause 1 (spread > 50% of mid) is NOT cosmetic — the registered decision rule says
        implement it.** Wide prints are ~6–8% of classified prints but only **0.32–0.43% of
        premium**, i.e. tiny trades — which is exactly why a premium-share test would have
        wrongly cleared it. The median name-day barely moves (|Δtilt| ≈ 0.005), but the TAIL does:
        **p95 |Δtilt| = 0.08–0.13**, 7–13% of name-days move more than 0.05, and the sign of the
        bull/bear call **flips on 2.2–4.0% of name-days**. A 2–4% flip rate is the same order of
        magnitude as the detector's own firing rate, so these prints are corrupting the statistic
        at a rate comparable to the signal being measured. Pre-registered rule was "does the tilt
        MOVE", not "how many prints" — and it moves.
      - ⛔ **Clause 2 (drop name-days >20% dropped) is NOT a data-quality filter as written — it
        is a 65–80% universe cut, and it lands on exactly the names the study is about.**
        Measured: **2,973–3,862 of ~4,600 name-days per session would be discarded (64.8–80.4%)**,
        yet they carry only **2.6–12.3% of premium** — i.e. it deletes the thin end wholesale.
        Mechanism: illiquid contracts frequently have absent or zero quotes, so thin names fail
        the dropped-share test almost by construction. **This is the same pathology A1 already
        diagnosed in the $250k day floor** — a filter that renders the illiquid bucket unaskable —
        and implementing it literally would destroy the Q1/Q5 analysis that is currently the
        study's strongest result. It cannot be implemented as registered.
      - **Correction to the cost estimate I gave the founder:** I said a re-roll was "days of
        compute". The 251-session tape decode took **46 minutes**, so a re-roll is hours, not
        days. The decision should be made against hours.
      - **Recommended dispositions (founder's call, both registered changes):** implement clause 1
        and re-roll; **amend** clause 2 rather than implement or retire it — the intent (drop
        name-days whose picture is unreliable) is sound, the 20%-of-all-prints threshold is not,
        and a threshold on the *classified* base or a much higher cut needs registering with its
        own measured justification. Clause 3 (whole-market demeaning) is untouched by this run and
        remains separable — no re-roll either way.

      **1.25 DISPOSITION — REGISTERED 2026-07-29, BEFORE THE CODE CHANGE. Founder: "let's do it."**
      - **Clause 1 — IMPLEMENTED AS REGISTERED.** `spread > 50% of mid` now fails the quote gate,
        so those prints become `side = unclassified` and never enter `bull`/`bear`.
        *Implementation choice, declared because "dropped" literally means removed:* the rows are
        **kept and marked unclassified rather than deleted.** The tilt is identical either way
        (its denominator is `bull + bear`), and keeping them means the discarded volume stays
        auditable and clause 2 is computable from the same pass. Nothing that feeds the tilt or
        the nulls sees them.
      - **Clause 2 — AMENDED, not implemented and not retired. The BASE changes; the registered
        20% threshold does NOT.** New rule: a name-day is excluded when **> 20% of its
        usable-quote prints were wide-spread** — where the old rule counted *all* dropped prints,
        including those with no quote at all.
        *Why the base was the defect:* the original conflates two populations. A print with no
        quote **cannot be labelled**, so it is already outside `bull + bear` and corrupts nothing;
        a **wide-spread** print gets a label that is close to a coin flip, and that is what
        justifies discarding the day. Missing quotes are a structural property of illiquid
        contracts, not evidence a day is unreliable — which is exactly why the registered version
        deleted 65–80% of name-days while touching 2.6–12.3% of premium.
        ⚠️ **The 20% number is deliberately held fixed.** Only the base is corrected, so this is
        not threshold-tuning; had the number been re-picked after seeing the drop rates, it would
        have been fishing. Whatever this excludes, it excludes.
      - ⛔ **Clause 2 — RETIRED 2026-07-29 (founder call), superseding the amendment above after
        it was measured and failed.** The amendment cut exclusions from 64.8–80.4% of name-days
        (2.6–12.3% of premium) to **44.7–60.8%** (1.4–6.7%) — better, and still deleting more than
        half the name-days, concentrated in the thin tier where the Q1/Q5 result lives. My stated
        reason for the amendment (no-quote prints were the driver) was only partly right: thin
        names genuinely have wide spreads, so the base fix could not have been enough.
        **Retirement rests on three mechanisms that already do clause 2's job:** (1) clause 1
        gives wide prints **no label at all**, so they cannot bias `bull`/`bear`; (2) the
        registered **$250k floor** already drops name-days without enough labelled premium to
        read; (3) — the argument that decided it — a name-day with many wide prints has fewer
        LABELLED prints, so its tilt rests on less data, and **that is exactly what `N_eff`
        measures and what the Addendum B base rate already prices in** (1.96% thin → 8.63%
        liquid). Clause 2 therefore guards nothing the machinery does not already handle, at the
        cost of half the corpus.
        ⚠️ **Registered as a retirement, not deleted from the record.** The prereg text stands;
        this is the amendment that supersedes it, with the measurement that justified it. If a
        later finding shows unlabelled-print density biases tilts *beyond* what `N_eff` captures,
        this is the entry to reopen.
        ⚠️ **Bug disclosed:** `tools/prereg_clause_impact.py` first reported 2,297 exclusions
        where the rollup reported 2,583 on the same session — its amended denominator added
        `wide` to a base that already contained it, understating the exclusion rate and
        flattering my own amendment on both count and premium. **Caught only because two
        independent implementations of one rule disagreed;** a single implementation would have
        shipped the wrong number. The rollup was correct. Both now agree exactly.
      - ★ **AUDITOR PUSH-BACK 2026-07-29, PARTLY SUSTAINED — and it produced a better answer than
        either side proposed.** The auditor's objection to the retirement: *"N_eff prices
        concentration, not quote validity. Variance ≠ validity."*
        - ✅ **Sustained: my stated argument overclaimed.** `N_eff` prices the *sample size* of the
          surviving labelled orders. It does not certify that the labels are sound. Conflating
          those was a real error in the reasoning, independent of whether the conclusion holds.
        - ❌ **Not sustained as applied, because clause 1 was RETAINED.** The push-back's remedy
          — "keep some spread/quality gate" — is exactly what clause 1 is, and it ships. Only the
          **name-day exclusion** was retired. After clause 1 a wide print carries **no label at
          all**, so the "garbage quotes set the sign" failure mode it describes cannot occur:
          nothing garbage sets any sign.
        - ★ **The real residual gap is neither variance nor validity — it is REPRESENTATIVENESS,
          and nobody had named it.** A name-day whose tilt survives on 40% of its quoted prints
          is not compositionally the same object as one keeping 95%, even when every surviving
          label is sound. `N_eff` does not price that, and clause 1 does not fix it.
        - **⇒ Resolution: record it, do not gate on it.** `wide_spread` joins the `SIDES`
          vocabulary at v7, so clause-1 casualties are no longer pooled with no-quote prints and
          the wide-spread volume per name-day is recoverable per bucket. Grading can then
          **condition on** wide-share and test whether it matters — strictly more informative
          than either dropping those name-days (the gate deleted 45–61% of them) or ignoring the
          issue. Verified on 2026-07-23: `wide_spread` = 595,928 prints / $0.149B against ~$46B
          session premium. Consumers filtering `side IN ('buy','sell')` are unaffected.
        - ❌ **Not sustained: "don't lean on the null curve until it is re-run on the same tilt
          definition."** The null never reads `side` or `right` — it is unsigned per-order premium
          times a symmetric coin flip, and `ε × right_sign` is distributed identically to `ε`
          while `bull + bear` partitions the same classified set as `buy + sell`. The curve is
          **exactly** axis-invariant; this was argued above with the mechanism and the push-back
          re-asserts rather than engages it. It IS being re-run on v7 — because clause 1 changes
          which prints are classified, not because the axis matters.
        - ✅ **Adopted as standing practice:** report **name-day survival AND premium share
          together, always.** They told opposite stories here (half the name-days, 1–7% of the
          money) and either alone is misleading.
      - **Clause 3 — untouched, still open.** Statistic-level, no re-roll, decided separately.
      - **Consequences accepted up front:** `SCHEMA_VERSION` 6 → 7, a full 251-session re-roll,
        and re-running everything downstream that reads the rollup or the tape — the base-rate
        table (Addendum B), the axis measurement, and 1.8's `netflip`. Their current numbers are
        superseded on landing, and any that move materially must be re-recorded, not quietly
        replaced.
      - **V7 RE-RUN COMPLETE (2026-07-29).** Re-roll: 256 sessions, 2h06m, `stale_spot=0`
        throughout, wide-print exclusion 3.0–6.8% of quoted prints per session (mean ~4.1% over
        the 2025 half, 4.9–5.9% in the busy 2026 tail). All three downstream measurements re-run
        against v7; **nothing moved materially — every v6 conclusion stands verbatim**:
        - *Base-rate table:* Q1 1.961% / Q5 8.629% vs v6's 1.96% / 8.63%; B2 STATIONARY (0.63pt
          vs 2.0pt bar); B1 still NOT MET on SHORT_OTM (Q1 tested=680 < 1,000) — the thin-tier
          sample problem is structural, not schema. Same seed ⇒ identical ε-draws, so the
          residual deltas are the pure clause-1 effect: negligible.
        - *Axis measurement:* corr +0.403/+0.241/+0.109 and opposite-sign 31.8%/36.8%/39.2% at
          the $0/$250k/$1M floors (v6: +0.404/+0.235/+0.102, 31.7%/37.0%/39.4%). Name-days
          938,453 vs 1,073,060 — clause 1 de-labels thin name-days entirely; disagreement
          finding unchanged.
        - *1.8 netflip:* single dir +26.4B→+10.1B **no flip** (v6 +27.9B→+11.4B), single side
          −16.1B→+47.0B FLIPS (v6 −13.7B→+48.3B), idx dir −93.5B→+9.4B FLIPS, idx side
          −95.5B→+20.0B FLIPS; per-session flip counts 47/80/117/112 vs v6's 48/79/118/112.
        - The pre-stated expectation (premium-weighted results barely move; sign-sensitive ones
          have the most room to shift) held and overshot — even the flip counts moved ≤1 session.
        v7 is now the corpus of record; v6 numbers above are retained as the audit trail.

**Phase 1 is done when:** the corpus is re-rolled clean, the four numbers exist, and Addendum F is
committed. Only then does anything below produce a trustworthy result.

---

## Phase 2 — open interest (open vs close)

**The gap:** buy-to-open a call is a bullish bet; buy-to-close a short call is someone covering.
Same print, opposite meaning. We cannot currently tell them apart, and this split is where the
academic predictability actually lives. No retail platform has it.

**Cost: $0.00, and no deadline** — OPRA `statistics` is L0-tier, so unlike `tcbbo` it does not age
out of a rolling window. There is no rush; do it after Phase 1.

- [x] **2.1 Confirm OI is in the payload.** DONE 2026-07-28 (whole-market pull of 2026-07-24,
      $0.00 quoted for one day AND the full corpus window): `stat_type=9` = OI, 34.3M records /
      1,976,143 instruments, 100% matched to that day's definitions, **6,277 of 6,294 underliers
      (99.7%)**. Zero OI is published explicitly (40.7% of records) so deltas see zero crossings.
      OI `ts_event` lands 06:30–08:44 ET **pre-open** ⇒ day-D's value = D−1's close positions —
      confirms the §5 T+1 leakage rule. Cross-publisher check (~17 publishers/contract):
      **0 disagreements** — OI is one OCC number republished per exchange; last-write-wins is safe.
- [x] **2.2 Backfill** `statistics` across the corpus window — `tools/opra_oi_backfill.py`,
      RUNNING since 2026-07-28 (~13–17 h, niced + IO-idle, `opra-oi-backfill.service`, log
      `data/opra_oi_backfill.log`). Condense-on-arrival: raw day (678 MB) → OI-only npz
      (`data/databento/opra_oi/YYYY-MM-DD.npz`, iid+oi, ~4 GB total) and raw DELETED — safe
      because statistics is L0 with no rolling window, every re-pull is $0. Per-day cost gate
      aborts the run on any nonzero quote. Never concurrent with another ALL_SYMBOLS pull.
- [ ] **2.3 Reconcile prints against daily OI change** per contract to estimate opening vs closing
      interest. *Known limit:* OI is a daily net, so this is an aggregate estimate, never a per-print
      label. Say so wherever it is surfaced.

      **2.3 spec (registered 2026-07-28, before build):**
      - **Alignment (measured 2026-07-28):** the statistics file for day D publishes 06:30–08:44 ET
        pre-open ⇒ it is the position count after **D−1's** close. The OI change attributable to
        session D's trading is `ΔOI_D = oi[file D+1] − oi[file D]`, taken over consecutive *session*
        files (holiday markers skipped). Sessions missing either side abstain.
      - **Contract identity:** join across days on the **OCC symbol via each day's definitions**,
        never on raw `instrument_id` — id stability across sessions is unverified; the build
        MEASURES it (report the mismatch rate) but the symbol join is the rule either way.
      - **Estimator, per contract-session with tape volume V > 0:** opening-volume fraction
        `f_open = (V + ΔOI) / (2V)`, clipped to [0,1]. Derivation: all-opening trading gives
        ΔOI=+V, all-closing gives −V, so ΔOI = V·(2·f_open − 1). Zero OI is published explicitly
        (40.7% of records), so zero crossings are real data, not gaps.
      - **Flags, not silent drops:** `|ΔOI| > V` (exercise/assignment/adjustment/off-tape activity —
        OI can move without prints), expiration-day and post-expiry removals (OI dies without
        closing trades — excluded from f_open), contracts absent from either day's defs,
        V=0-with-ΔOI≠0. Every flag count is reported in the output; nothing is silently discarded.
      - **Honest ceiling (registered):** ΔOI is a daily NET — a day with 500 opens and 500 closes
        shows ΔOI=0 and f_open=0.5. f_open is an aggregate estimate consistent with the bounds,
        never a per-print label; every surface that carries it says so.
      - **Output:** per (occ_symbol, session): V, ΔOI, f_open, flags; plus per-underlying/dte-bucket
        session rollups shaped for 2.4. Construction/measurement lane — 2.4's re-grade gets its own
        pre-registered pass/fail criteria before it runs.

      **2.3 BUILT + first measurements 2026-07-28** (`tools/oi_reconcile.py`): clean coverage
      **98.6%** of traded contracts (271,073/274,799 on 2025-07-28); flags per session ≈ 0.3–0.6%
      `|ΔOI|>V`, ~1,000–1,250 expired, 1,800–3,700 absent-from-one-day's-OI. Vol-weighted
      `f_open` = **0.659 / 0.634 / 0.630** on 2025-07-28/29/30.
      - ⚠️ **`instrument_id` is NOT stable across sessions — measured 0.000–0.001 overlap.** The
        spec's OCC-symbol join was load-bearing, not caution; any cross-session work on this data
        that joins on raw `instrument_id` is wrong.
      - ⚠️ **`f_open` > 0.5 is EXPECTED, not a finding.** OI also decays through exercise,
        assignment and expiry — none of which is a closing *trade* — so opens must exceed closes
        just to hold OI flat. The absolute level is therefore uninterpretable as "63% of trades
        open"; the feature is meaningful only CROSS-SECTIONALLY (which contracts/underliers run
        more opening than the population), and 2.4 must use it that way.
      - ⚠️ **tcbbo `size` carries the uint32 UNDEF sentinel** (4,294,967,295) on ~0.35% of prints
        (~24k/session). Unmasked it computes a session premium of $2.4e16 instead of $26.3B. The
        v6 corpus is UNAFFECTED — **verified**: 0 of 24,298 sentinel prints resolve to that day's
        definitions, so `flow_corpus_rollup.py`'s instrument join drops them all before premium is
        computed. Note the protection is incidental (an unresolvable id), not an explicit guard.

      **2.3 FULL-CORPUS RUN COMPLETE 2026-07-29** — `oi-reconcile-full.service`, 40m51s, 7.5G
      peak, `data/oi_reconcile.db` (444 MB) + `data/oi_reconcile.log`. **Construction lane is
      DONE; 2.4 is unblocked.**
      - **`instrument_id` instability confirmed corpus-wide, not just on 3 sessions:**
        iid-stability **0.0000–0.0010** every session. The OCC-symbol join was mandatory.
      - **Clean coverage 95–98% on ordinary sessions**, falling to **~80–87% on monthly-expiry
        days** where `expired` and `no-oi-both-days` spike together (2026-06-18: 70,007 expired /
        80,367 no-oi; 2026-07-17: 62,618 / 68,225). Expected — OI dies at expiry without a closing
        trade — and the flags are reported, not silently dropped, exactly as registered.
      - **Vol-weighted `f_open` runs 0.60–0.70** across the corpus, with the high end landing on
        expiry-adjacent sessions. ⚠️ **Still not interpretable as "65% of trades open"** — the
        registered caveat above stands unchanged, and 2.4 may use `f_open` **cross-sectionally
        only**.
      - ⚠️ **NEW — the `size` UNDEF sentinel DISAPPEARS FROM THE FEED between 2026-06-22 and
        2026-06-29, and this is a corpus regime break.** Verified directly against the tcbbo tape,
        not inferred from the OI pipeline: 2026-06-22 = 47,103 sentinel prints, 2026-06-29 = **0**,
        2026-07-23 = **0**. It also roughly DOUBLED before vanishing — the 2025-07-28 note above
        measured ~24k/session, and 2026 sessions run ~47–48k. Consequences: (i) the explicit
        sentinel guard added for A2 is load-bearing for ~11 months of the corpus and a no-op for
        the tail, so it must not be removed as dead code; (ii) **open question, not chased here:**
        whether those ~48k prints/session now arrive with REAL sizes — in which case post-06-26
        sessions carry flow that earlier sessions dropped, a systematic composition change across
        the corpus — or simply stopped being published. Session print totals are flat across the
        break (11.56M → 11.59M), which does not settle it either way.
- [x] **2.4 Re-grade the Phase 1 signal split by open vs close.** *Decides:* whether the split
      recovers the predictability the literature reports. **RAN 2026-07-29: FAIL — recorded per
      the registered limitation as "not recoverable with free ΔOI data," not "open/close carries
      nothing." See the RESULT block below the registration.**

      **PRE-CHECK RUN 2026-07-29** (`tools/f_open_dispersion.py` on `data/oi_reconcile.db`,
      vol floor 1,000 contracts/name-day, ~1,195 qualifying names/session) — asked BEFORE
      writing the prereg whether the feature can discriminate at all. **It can; 2.4 is
      runnable**, and the compression worry raised 07-29 is measurably WRONG:
      - **Cross-name spread is wide, not thin.** p10=0.535 / p50=0.685 / p90=0.870 —
        a 0.335 p90−p10 spread, cross-sectional std 0.139. The 0.60–0.70 range that
        prompted the worry was the *vol-weighted aggregate*, which compression-averages
        the very dispersion 2.4 needs. Aggregate compression ≠ cross-name compression.
      - **The spread is NOT the expiry calendar.** DTE gradient of the vol-weighted mean
        is only 0.573→0.687 (short→long), while the *within-bucket* cross-name spread is
        0.30–0.42 in every bucket — the name effect dwarfs the DTE effect. Excluding
        DTE 0/1–4 entirely leaves the spread at 0.340 (vs 0.335 with them). The calendar
        artifact lives in the aggregate time series, not the cross-section. (DTE-0 cells
        are already absent by construction — expiry-day f_open is NULL per 2.3's flags.)
      - **It is a partly persistent name trait, not day noise:** day-over-day Spearman
        rank autocorr 0.289 (lag 1) / 0.203 (lag 5), ~10 SE from zero at this breadth.
        Persistent enough to condition on; the non-persistent remainder is the event-day
        variation 2.4 actually wants to read.
      - **What the prereg must therefore gate on** (constraints, not yet the registration):
        cross-sectional rank/percentile of f_open per session, never levels; DTE held
        fixed or ranked within bucket; monthly-expiry sessions flagged into the
        robustness subset (clean coverage dips to 80–87% there per 2.3); vol floor
        stated up front. Pass/fail criteria still to be registered before any graded run.

      **2.4 PRE-REGISTRATION — REGISTERED 2026-07-29. Drafted at founder direction
      2026-07-29; two report-only legs (iv)/(v) added from founder review the same day;
      founder approved ("ok lets go") 2026-07-29 with the legs included. BINDING from
      approval — changes from here require a dated addendum.**

      *The question in one sentence:* when the Phase-1 flow alarm fires on a name, does the
      alarm work better on days when that name's options volume was mostly NEW positions
      being opened, versus days when it was mostly old positions being closed out?

      *Why this split could matter:* opening a position is placing a fresh bet; closing one
      is walking away from an old bet. The academic result this phase chases (Pan &
      Poteshman) found the information lives in **opening** volume. If our alarm fires on a
      day that was mostly closing traffic, the "conviction" it saw may just be people
      leaving.

      - **Event set.** Every Phase-1 event (registered tilt, `|z| ≥ 2.0`, 20-day baseline,
        ≥15-of-20 presence, $250k floor — prereg 2026-07-26, unchanged) in the **training
        window only** (2025-07-28 → 2026-05-31). The test split (2026-06-01 → 2026-07-25)
        is not touched.
      - **The split.** For each event name-day, take `f_open`'s **percentile within that
        session's cross-section** (all names with ≥1,000 contracts that day — the
        pre-check population). Top third = **open-heavy**; bottom third = **close-heavy**;
        middle third set aside (reported, no pass/fail authority). Ranks, never levels,
        per the 2.3 registration.
      - **Outcome.** Signed terminal alpha at the 10-trading-day horizon of record (1.17):
        `sign(event tilt) × (name return − SPY return)`, in ATR units. Bullish events want
        up, bearish events want down — shorts are first-class per ADR-6.
      - **Primary statistic.** Median signed alpha, open-heavy minus close-heavy, in the
        **primary cell**. The primary cell is ONE liquidity quintile (1.23 forbids pooled
        kill authority), chosen by a registered outcome-blind rule: after events are
        counted but **before any outcome is computed**, the quintile with the most
        independent event dates on BOTH sides of the split is declared primary in a dated
        addendum. Every other cell is exploratory and labelled so.
      - **PASS** = open-heavy median exceeds close-heavy in the primary cell AND the
        difference's 95% interval (bootstrap resampling whole event DATES, per 1.18's
        clustering logic) excludes zero. **FAIL** = interval includes zero or points the
        wrong way. **UNEVALUABLE** = either side has <20 independent event dates in the
        primary cell (1.18's bar, per side) — declared, not massaged into a verdict.
      - **Noise correction per 1.23:** both sides of the split grade against the SAME
        per-quintile base-rate table (Addendum B) — the split cannot manufacture lift by
        moving events between tiers, and `lift_real = lift_obs / (1 − f₀/f)` applies
        within tier as everywhere else.
      - **Robustness legs (report-only, no authority):** (i) recompute f_open on DTE 5–89
        cells only; (ii) drop events on monthly-expiry sessions (2.3: clean coverage dips
        to 80–87% there); (iii) tercile edges ±5 percentile points, to show the verdict
        is not an artifact of the 33/67 cut; (iv) **dose-response gradient** (founder,
        07-29): events lined up by f_open percentile (quintiles of rank), median signed
        alpha per bucket — a real effect should climb with the dose, a fluke shows up in
        one bucket and vanishes in the next; (v) **same-side f_open** (founder, 07-29):
        f_open recomputed on the event's dominant right only (calls for call-driven
        events, puts for put-driven — dominant = the right carrying the majority of the
        event day's bull/bear premium), since "the calls were open-heavy" is sharper for
        a call-driven bullish event than the whole name's blend. ⚠️ Registered honesty
        note on (v): ΔOI cannot see WHICH party opened — an aggressive call WRITER
        raises OI exactly like an aggressive call buyer; the leg stacks evidence
        (aggressor side from quotes + newness from ΔOI + location from right), it does
        not recover the true label.
      **2.4 STAGE-1 COUNT + PRIMARY-CELL DECLARATION (2026-07-29, outcome-blind —
      no forward return computed or read).** `tools/open_close_split.py`: training
      window 2025-07-28 → 2026-05-29 (212 sessions, 6,491 names), **16,598 events on
      192 distinct sessions**; f_open ranks 87.9% of events (unmatched = below the
      1,000-contract floor or absent from the OI join — reported, not dropped).
      Independent event dates open/close per quintile: Q1 0/1, Q2 4/3, Q3 41/34,
      Q4 189/179, **Q5 192/192**. ⇒ **PRIMARY CELL = Q5** by the registered rule
      (max per-quintile min of the two sides), and it clears 1.18's ≥20-per-side bar
      with room. Q4 and Q3 also clear and grade as labelled exploratory cells; Q1/Q2
      are UNEVALUABLE (declared in advance, consistent with every earlier thin-tier
      finding). Noted for honesty: Q5 is also the tier with the HIGHEST noise floor
      (8.63% base rate), so the 1.23 dilution correction does the most work exactly
      where the verdict lives — both sides grade against the same table, so this
      cannot favor either side. Stage-2 implementation choice disclosed in advance:
      ATR = 14-day daily-bar ATR, applied identically to both sides of every
      comparison, chosen before any outcome was seen.
      - **Registered limitation, stated up front:** `f_open` is a **name-day, unsigned**
        estimate — it says "today's volume in this name skewed toward opening," not
        "the bullish side was opening." The literature's instrument was signed
        open-buy volume; ours is blunter. A FAIL here is therefore evidence about the
        proxy as much as the phenomenon, and is recorded as "not recoverable with free
        ΔOI data" — NOT as "open/close carries nothing." A Cboe-style true label could
        reopen it; per the founder's standing rule that purchase needs profits first,
        and per the coverage note it would only label Cboe's slice of single-name flow
        anyway.

      **2.4 RESULT (2026-07-29) — FAIL, single graded run, verdict per the registration.**
      `tools/open_close_split.py --stage grade`: 16,102 of 16,598 events graded (496
      skipped for missing bars/ATR window, reported). **Primary cell (Q5):
      open − close = −0.043 ATR, bootstrap 95% CI [−0.133, +0.046] — includes zero ⇒
      FAIL.** The point estimate even leans the WRONG way (close-heavy marginally
      better), opposite the literature's direction.
      - **Every robustness leg agrees; nothing hints the verdict is fragile:** tercile
        edges ±5pt (−0.052 / −0.012, both zero-straddling), ex-expiry (−0.048),
        DTE 5–89 (−0.059), exploratory Q4 (+0.078, CI [−0.24, +0.35]) and Q3 (n too
        small to mean anything). **The dose gradient — the founder's leg — is the most
        conclusive negative:** +0.026 / +0.000 / +0.046 / −0.058 / +0.041 across the
        rank quintiles. No climb, no shape; a real dose-response does not look like
        that.
      - **The same-side sharpening (leg v) did NOT rescue it** (−0.073,
        CI [−0.189, +0.007]) — if anything the sharper instrument leaned further
        negative. The blunt-proxy explanation predicts the sharpened leg should look
        BETTER if a real effect were being blurred away; it looked worse, which
        shifts weight from "proxy too blunt" toward "no recoverable effect at this
        grain," though the unsigned limitation still caps what the run can say.
      - **Recorded conclusion, exactly as pre-registered:** the open/close split of
        Phase-1 events is **not recoverable with free ΔOI data** at the name-day
        grain. NOT recorded: "open/close carries nothing" — the instrument cannot
        support that claim. Reopen conditions: a true per-trade label (Cboe, subject
        to the standing no-purchase rule + the single-exchange coverage caveat) or a
        materially sharper free proxy (e.g. Phase-3 hedge-footprint signing, which is
        direction-signed by construction).
      - Fail stays on the record; no re-run, no re-tune. Phase 2's construction
        artifacts (`oi_reconcile.db`, `f_open`) remain valid perception inputs —
        what failed is this SPLIT as a grading axis, not the data.

      **2.4 ADDENDUM A — registered 2026-07-29 BEFORE running (founder question: does
      the 10-day horizon apply here?).** It does, and it is the one registered choice
      that could hide something: the cited literature's effect is strongest at d+1 and
      REVERSES through d+10 — a pop-then-fade shape nets to ≈0 at exactly the horizon
      we graded. So: an **exploratory horizon curve at 1, 2 and 5 trading days** on the
      primary cell (same events, same split, same statistic, only the horizon moves).
      **Report-only, declared in advance: this look has NO authority over the 2.4
      verdict**, because the 10-day FAIL has been seen and picking new horizons after a
      fail is the exact fishing the law forbids. If short-horizon structure appears, it
      becomes a REGISTERED hypothesis to confirm on fresh data (test split or forward
      paper) — it never retro-passes 2.4.
      **ADDENDUM A RESULT (2026-07-29, same day):** 1d +0.000 [−0.029, +0.026] ·
      2d +0.009 [−0.027, +0.049] · 5d +0.008 [−0.063, +0.063] · 10d −0.043 (the
      verdict). **Flat zero at every horizon — there is no day-1 pop for a reversal
      to hide.** The pop-then-fade escape hatch is closed by measurement, and the
      1-day interval is the TIGHTEST of the four (±0.03 ATR), so this is a precise
      zero, not an uninformative one. The 2.4 FAIL is now robust across the
      time-frame dimension; no short-horizon hypothesis is registered because
      nothing was found to register.

- [ ] **2.5 (PARKED — founder idea 2026-07-29, not registered, not scheduled).** Where OI sits
      HEAVIEST — which strikes, which expiries — and whether price behaves differently near those
      concentrations: pinning to big-OI strikes into expiry, "gamma walls" that dampen or repel
      price, max-pain drift. Attractive precisely because it is **direction-free** — it asks where
      the walls are, not who is bullish — so it dodges the unsigned-proxy wall that killed 2.4.
      The academic pinning literature (delta-hedge pinning at high-OI strikes on expiry days) is
      real. ⚠️ If opened: any GEX-style computation requires ASSUMING who is short the gamma
      (dealer vs customer) — OI cannot sign that any more than it could sign 2.4 — so the
      dealer-positioning assumption must be registered explicitly, not inherited from retail
      GEX lore. Parked until Phase 1c lands.

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
