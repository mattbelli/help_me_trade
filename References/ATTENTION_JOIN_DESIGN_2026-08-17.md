# Attention list — audit of the two detectors + design of the join

*2026-08-17 · founder ask: "audit each detector against what our studies said, then design how the two
join into ONE list." Inputs: two independent read-only Opus audits (abnormality scanner #296;
flow rarity×shape #310/#311), each spot-checked by the orchestrator against the live DB and the
registered artifacts (`data/research/mover_intraday_114g.json`, `FLOW_ROADMAP.md`). Status: PROPOSAL —
nothing here is built. Companion: `ABNORMALITY_SCANNER_DESIGN_2026-08-13.md`, BUILD_LOG 08-12 (rarity)
and 08-14 (abnormality) rows.*

## 0. The one-paragraph verdict

Both detectors compute the right *primitives* faithfully (Parkinson range vs own time-of-day norm;
per-name per-bucket rarity of an option print). Both then wrap those primitives in **firing rules and
ranking multipliers that no study supports** — and in the flow case the shape kernel points *against*
the one shape finding the corpus replicated three times. Neither surface today shows what the studies
license. The join therefore is not "sum two scores": it is (1) strip each detector back to its
licensed primitive, on the **same universe**, (2) light an *angle* per name-day, (3) rank by angle
count then within-angle magnitude, (4) grade every name-day (lit or not) with the study's estimand so
the list can be measured. Measured on today's tape the join is 12 names (see §4.4), not 276 fires and
not 909 "rare" prints.

## 1. Abnormality scanner (#296) — audit findings

Verified today (first live session): 276 fires, 139 of them within 60 min of the same name's prior
fire; top-10 by composite = SNYR, WOK, BRBS, PICS, OABI…; `abnormality_outcomes` = 0 rows (grader runs
tonight — legitimate, first session).

**Matches the study (1.14f/g):** Parkinson RV per-bin averaged, zero-range present-and-zero, ≥80% bins
else absent (`signals/abnormality/math.py:39-48`); trailing-60-min window `[b−12,b)` numerator and
denominator (`engine.py:90-97`, `baselines.py:143`); ≥10-of-20 session warmup; the ungapped window
(the study's one-bar gap was outcome hygiene — verified `FLOW_ROADMAP.md:1653-1655`); peer cell =
boundary × $-vol quintile; direction never claimed; labels survive every transport.

**Deviates / unbacked — ranked by consequence:**
1. **Cooldown is in-memory only** (`detector.py:67`, cleared by `_reset_session_unlocked`); the
   collector restarted ~7× today → 139/276 fires are within-60-min repeats. 1.14f measured **first
   fire per name-day only** and excluded later boundaries as contaminated pseudo-replicates
   (`ROADMAP:1566-1569`). Half of today's fires are the population the study threw away.
2. **Universe.** Arm 2a — the only measured equity-RV screen — ran on the **options-eligible pool**
   (~1,000–1,300 names/boundary; the artifact's `arm2a_note` forbids reading it as market-wide). Live
   fires on 12,429 names. Peer cells have **no occupancy floor** (`math.py:99-105`): at bucket 66 today
   q0 had 0 finite values, q1 had 71 — a one-name cell auto-passes p99. SNYR fired from q0.
3. **Fire rule.** Study = **p90 within cell, single component (range), no floor**. Live = p99 range ∧
   p95 volume ∧ floors 2.0/3.0 ∧ top-10 per boundary. The volume component, the AND, the percentiles,
   the floors and the budget have no study; the budget was today's binding constraint. Fires on every
   5-min bucket 12–77; the study grid is half-hours 10:00–14:00 (22 fires today after 14:00, 83% off
   the registered grid).
4. **Ranking = range_ratio × vol_ratio** (`math.py:71-76`) — both own-normalized, so the product is
   maximized by names with the smallest norms → microcaps on top. Unbacked.
5. **Caveat text is wrong.** "32–43% more than their time-matched norm" is the fired-minus-complement
   *gap*; the registered numbers are fired median **1.26–1.38× own norm**, complement 0.93–0.95×
   (`mover_intraday_114g.json arm2a_machinery_placebo`; primary cell 1.316 vs 0.938). And the effect
   exists only in own-normalized units (raw-denominator diagnostic sign-flips) — a fire is "moving
   more than *it* usually does", never "about to move a lot".
6. Replay harness applies ONE baseline artifact to every replayed session (`replay.py:130`) → the
   184 fires/day figure cannot lock thresholds; thresholds still DRAFT. `median_fwd_2h` skips the warm
   mask (`baselines.py:153-156`). Nightly rebuilds baselines BEFORE grading, so today's forward norm
   includes today (registered rule: denominators use ≤ t−1). Outcome estimand uses high/low excursion
   in simple returns; the study uses max |cumulative 5-min log return| on closes — not comparable to
   1.34/0.94. `abnormality_state` never expires (Friday rows read `live` all weekend). CA guard runs
   only at bucket 12 and compares the minute **high** to prior close (`detector.py:157`). Base-state
   params never founder-eyeballed (Inc 3 open); `weeks_in_base` saturates at 6.
7. Every constant in `constants.py:24-35` (p99/p95/2.0/3.0/N=10/60min/1.5×/CA 15%) is unbacked.

**What the study licenses us to say about a fire:** on the options-eligible universe, a name whose
trailing-60-min range is in the top decile of its (boundary × $-vol quintile) cell went on to a next-2h
max excursion of ~1.3× its own time-of-day norm (non-fired: ~0.94×), direction a coin flip, effect
present only relative to the name's own baseline.

## 2. Flow rarity × shape (#310/#311) — audit findings

Verified today: 8,413 sweep/block rows, 7,765 stamped, 927 at p<0.05; conviction top-100 all at
60–65 DTE (one monthly expiry), median premium $68k vs tape median $112k; the day's six largest prints
(SNDK $172M … META $62M) rank #7,331–#8,571 at score 0.04.

**Matches:** cell definition (name, right, dte_bucket, mny_bucket), 120 sessions, ITM own bucket,
n<100 unranked; live sweeps ARE package-fenced via condition codes (`sweeps.py:42-57`); known-side
bonus dropped (consistent with 1.14 NO-SIGN); honesty string on widget + agent description.

**Deviates / unbacked — ranked by consequence:**
1. **Wrong reference class.** Live premium = parent-order **sum** (`sweeps.py:137`); corpus "peak" =
   largest single **print** per session (`flow_corpus_rollup.py:514-515`). Median historical peak in
   today's top cells is $1,350–$5,590 vs live sums $50k–$191k. So `rarity_p` mostly says "this bucket
   is normally retail-sized" — a sparsity fact, not an unusualness fact. Measured: **22% of today's
   active cell-days clear a nominal 5% bar** (calibration claimed ~5%). The registered feature is
   single-print on both sides (`largest_print_ratio`, `ROADMAP:798-800, 1556-1559`).
2. **The tenor kernel inverts the studies.** Short-dated (≤30 DTE) is the ONLY replicated positive:
   1.13 FINDING 1 (`:923-927`), 1.14c (`:1305`), 1.14d "the one persistent positive" (`:1388`), 1.14e
   "a THIRD time" (`:1478`); the academic prior says the same. Kernel peaks at 55 DTE, haircuts ≤7 DTE
   ×0.45 (`dte_kernel(4)=0.27` vs `(55)=0.995`). No doc records a decision to override. Zero DTE≤7 rows
   in the top-200.
3. **Shape, not rarity, ranks the list.** Rarity weight spans 0.91–0.99 across the top-100 (~9% of
   range); `dte_w×otm_w` spans 23×. The list is "sweeps near the next monthly at ~10% OTM" — a
   calendar artifact. Rarity and shape also double-count: 31-90 DTE is both the kernel peak and the
   sparsest (lowest-p) bucket.
4. **Size is invisible and the ranking is anti-correlated with it** — the score never reads
   `premium_usd`; FINDING 3 (abnormal largest print, 1.2–1.4× in every size tier) and the intraday
   `largest_print_ratio ★` say size-vs-own-normal carries something.
5. Reference class not multileg-fenced (numerator is) — conservative bias, unmeasured; moneyness
   bucketed on live spot vs corpus prior close (~3% of prints shift cells); per-event stamping with no
   per-name-day dedup (942 events from 675 cell-days); ETF root fence not applied under
   `order=conviction` (10/100 ETFs today); calibration sampled 400 cells in dict order (~13 names);
   nightly backfill computes "since 00:00 ET today" at 00:30 ET → stamps 0 every night; `conviction`
   collides with two other unrelated `conviction` fields on the agent surface; the HTTP payload
   carries no honesty string; every kernel constant (`db/flow_conviction.py:22-40, 87`) unbacked.

**What the studies license us to say about a rare print:** a bursty, outsized, **short-dated** print is
part of a fingerprint that *describes* big-move windows (1.4–1.7× backwards enrichment vs matched
peers); at intraday grain a name's own outsized single print precedes larger-than-baseline movement
*magnitude*; no direction; no forward selection power at day grain; the honest per-print sentence is
"larger than X% of this name's own daily maxima in this right/tenor/moneyness bucket".

## 3. Design principles for the join (what the audits force)

- **Angles, never a composite.** Fusion's failure was multiplying/adding unrelated scales. Each
  detector lights a boolean angle per (name, session) plus its own magnitude percentile; the list
  ranks by (angles lit, then max within-angle percentile). No weights exist to argue about.
- **Same universe.** Both angles restricted to the options-eligible pool = names in the flow rarity
  lookup (n ≥ 100 active sessions; 3,705 today; 2,048 in the top $-vol quintile). This is Arm 2a's
  population and it removes the microcap problem at the root, not at display. State can still be
  computed for all 12k (cheap), fires and the ledger only from the pool.
- **Study rule = the definition of "lit"; ranking = magnitude; display floor = liquidity.** Firing
  at the study's p90 will light more names than "tens/day"; that is fine because lit ≠ shown. The
  liquidity floor (default $-vol quintile ≥ 3, founder-tunable) and the angle count govern the list.
- **First lit per name-day only** (the study's object), persisted in the DB, not process memory.
- **Grade everything** — every name-day in the pool, lit or not, with the study's estimand — so we
  can measure "2 angles > 1 angle > 0" and later register stricter subsets (p95/p99, RV tier,
  base-state pairing) instead of guessing them.
- **Presentation = the licensed sentence + plain ratios.** No score column. "conviction" is renamed.

## 4. The design

### 4.1 Detector fixes that precede the join (each is a bug or a study-alignment, not a redesign)
Abnormality: (a) universe = rarity pool for fires; cell occupancy floor 25 (1.14g's own), cells with
fewer → no fire; (b) fire = range_ratio ≥ p90 within cell (single component), on half-hour boundaries
10:00–14:00 (state stays 5-min for the column); vol_ratio/rvol_session become displayed angles-within-
the-angle, not gates; (c) cooldown/first-fire persisted (read today's `abnormality_events` at
startup; DB unique on (symbol, session, kind) for the *first* fire, later fires stored with
`repeat=1`); (d) caveat text → "moved ~1.3× their own time-of-day norm over the next 2h vs ~0.94× for
non-fired peers; magnitude only, direction a coin flip"; (e) grader: close-based max |cum log return|
estimand, sweep BEFORE baseline rebuild, warm mask on `median_fwd_2h`; (f) state rows carry `session`
and the API filters on it; (g) replay uses per-session baselines; (h) CA guard uses last print as
close. Base-state stays an *annotation* (thesis, not counted as an angle) until Inc 3 eyeball.

Flow: (a) stamp rarity on **max single-print premium** of the parent order (retain it at cluster close;
`sweeps.py`) against the per-print corpus peaks — apples to apples and literally the study's
`largest_print_ratio`; the order sum stays as displayed size; (b) lookup built from `multileg=0` rows
only; moneyness bucketed on prior close; (c) calibration re-run on a random 400-cell sample and the
measured cell-day rate at α=.05 recorded (target ≈5%); (d) **retire the shape kernel as a multiplier**;
DTE/OTM/side become facts on the row; the ranking input from flow is rarity of the largest print +
premium; (e) per-name-day aggregation with dedup by cell-day; ETF fence applied; short-dated share of
the name-day's rare prints carried as a field (the replicated positive); (f) rename `conviction` →
`rarity`; honesty string in the payload; fix the nightly backfill window.

### 4.2 The ledger — `attention_daily` (trading.db), one row per (symbol, session)
```
symbol, session, quintile,                                -- pool + liquidity tier
abn_lit, abn_first_ts, abn_bucket, abn_range_ratio, abn_range_pctile, abn_vol_ratio,
abn_rvol_session, abn_repeats, base_state,                -- angle 1 (+ thesis annotation)
flow_lit, flow_first_ts, flow_cell_days, flow_min_p, flow_max_print_usd,
flow_call_prem, flow_put_prem, flow_short_dated_share, flow_events, -- angle 2
angles (0/1/2), rank_pctile (max of the two within-angle percentiles among lit rows today),
first_lit_ts, label, caveat, updated_at
```
Written by ONE backend job every 60s during RTH (one connection, one upsert): reads
`abnormality_events` (first fire per name today, ≥ occupancy-floor cells) and `options_flow_events`
(sweep/block, stamped, cell-day dedup, `rarity_p ≤ 0.05` on the corrected basis). Timestamps
normalized to UTC (abnormality stamps ET-offset, flow stamps Z). Nightly: `attention_outcomes`
(symbol, session, first_lit_ts, angles, quintile, excess_2h, excess_close, direction) for EVERY pool
name-day — computed from the 5-min store with the study estimand — so the aggregate "median excess by
angles lit" is a plain query, Wilson-bounded where a rate is shown.

### 4.3 Surfaces (wire-it-everywhere)
- `GET /api/attention?session=today&min_quintile=3&min_angles=1` → rows sorted by (angles desc,
  rank_pctile desc, first_lit_ts). Payload carries `label` + `caveat` on every row (the two licensed
  sentences), never a score.
- Watchlist "Unusual today" list: one row per name — `NXE  ▲range 3.2× vol 8.9×  ·  15 rare calls
  $2.3M (min p .008)  ·  Q4  ·  10:50` — click-to-chart, ticker links everywhere. The RVOL scanner
  list is retired into this (its rvol_session lives here); `flow_perception` becomes a nightly column.
- Perception: `RESOURCES["attention"]` (+ aliases `unusual`, `attention_list`) + per-symbol field in
  `get_context`; book pack "unusual today" line; `research/book_tools.py` + `book_session.sh` reach it.
- **AI queue, no pollution:** rows with `angles = 2 AND quintile ≥ floor` create one `look_into` item
  per name-day; a separate investigator run consumes one item with only that name's data and posts a
  short inbox note as perception. Nothing is injected into other prompts.

### 4.4 Measured on today's tape (before any fix — the shape, not the final list)
115 names fired (equity), 497 names had a "rare" print (flow, uncorrected basis), **20 had both, 12 in
the top quintile**: NXE (fire 10:50; 15 rare call prints, $2.3M), BHF (8 fires, base_high, $1.3M
calls), FIGR, XPEV, EIX, ARGX, LHX, STNE, HIVE, MSGS, NGG, SYF. After the fixes both counts fall
(first-fire-only, p90 single component on the pool; corrected rarity basis) — the join is expected to
land in the low tens per day, which is the target the abnormality brief set and never reached.

### 4.5 Sequence
0. Bugs (a day): cooldown persistence, caveat text, replay baselines, nightly order, ETF fence,
   backfill window, `spread_flag` dead column noted, honesty string in payload. `FUSION_ENABLED` gate,
   default off.
1. Study alignment (a few days): universe + occupancy floor + p90 single-component + half-hour grid;
   flow largest-print rarity + fenced lookup + prior-close moneyness + kernel retired.
2. Ledger + job + API + watchlist list + perception (a few days). Fusion attach lanes for both
   detectors deleted with fusion.
3. Outcomes on the ledger + the AI queue.

### 4.6 Founder forks (decisions only you make)
1. Fire rule: the study's p90 single-component (recommended — it is what is licensed; the list rank
   does the selecting) vs keep the stricter draft (p99∧p95∧floors) as an unregistered choice.
2. Liquidity floor for display: quintile ≥ 3 (top 40%, recommended start) vs ≥ 4.
3. Retire the shape kernel entirely (recommended) vs keep DTE/OTM as a *display* preference. The
   audit is unambiguous that as a ranking multiplier it contradicts the studies.
4. Fusion off now (recommended) vs after the ledger ships.
