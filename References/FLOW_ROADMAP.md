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
- [ ] **1.14b Split events by catalyst vs no-catalyst** (founder framing, 07-27: correlation breaks
      at catalyst moments — earnings, guidance, M&A, FDA). "Someone knew about the event" and
      "someone moved the stock" are different stories with plausibly different preceding-flow
      profiles; pooling them repeats the index/single-name mistake one level down. Earnings/opex
      flags already exist as covariates — this makes the split a registered look in the ledger.

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
