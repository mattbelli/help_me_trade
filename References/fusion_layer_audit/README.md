# Fusion layer audit — 2026-08-11

Seven Opus agents, read-only, measuring every perception channel against its own substrate.
Commissioned after the founder's question: *"what I am trying to accomplish is for the system to bring
unusualness forth and for the AI to dig into that… what I'm doing now is collecting a bunch of clutter."*

**The clutter has a specific, measured cause. This folder is the evidence and the plan.**

| file | channel | one-line verdict |
|---|---|---|
| [01-options-flow.md](01-options-flow.md) | options flow | **Works.** Rarity calibrates 0.97–1.08× out of sample on 261 days. Two fixable defects. |
| [02-dark-pool-blocks.md](02-dark-pool-blocks.md) | dark pool + blocks | **No substrate.** Prints never retained, 30-day prune. `equity_block` is 96% auction machinery. |
| [03-equity-price-volume.md](03-equity-price-volume.md) | equity price/volume | **Best hidden asset, frozen.** 251 sessions × 5,289 names on disk, no cron since 07-30. |
| [04-news.md](04-news.md) | news | **Suppressor, not evidence.** Marks the day the move already happened. 13 sessions deep. |
| [05-social.md](05-social.md) | social | **Measuring the wrong thing.** A fixed-size trending feed, not chatter. Reddit is broken. |
| [06-market-context.md](06-market-context.md) | market context | **Wrong shape.** Per-day facts stored as per-name rows. `market_shock`'s VIX arm is dead code. |
| [07-cross-channel-dependence.md](07-cross-channel-dependence.md) | dependence | **The architecture verdict.** Clusters are 59% company size. 8 of 15 channels can't fire alone. |

Every load-bearing claim in these files was independently re-run by the orchestrator against the live
databases before being reported. Verification notes are at the top of each file.

---

## The verdict in one paragraph

The perception layer does not measure unusualness. It measures **company size**. Four hardcoded dollar
thresholds mean a detector fires 954×–2,469× more often on the largest names than the smallest, so
"three channels agree on this name" mostly says "this name is large." Eight of the fifteen channels
cannot open an observation on their own — they can only attach to one another channel already started —
so everything piles onto the same few hundred names. Two channels are the same market-wide constant
written twice. One channel is 96% closing-auction re-prints. Three of the four direction labels agree
with each other at exactly chance. And the arithmetic meant to combine it all overselects by 117× in
the tail you would actually read.

**None of that is a tuning problem, and none of it is fixable by adjusting numbers.**

---

## ★ The distinction that decides the plan

**The data is good. The layer that interprets it is what's broken.**

These are separable, and they have completely different replacement costs.

**What is genuinely good — and cannot be re-acquired by working harder:**

| asset | size | state |
|---|---|---|
| `data/flow_corpus.db` → `flow_rollup` | 89,059,737 rows, **261 sessions**, 7,020 underlyings | Clean. `schema_version` 7 throughout. **Zero unexplained calendar gaps** — the only 10 missing weekdays are the 10 market holidays. |
| `data/equity_bars_5min/` | **251 sessions × ~5,900 symbols**, 1,482,456 symbol-days | Complete, zero gaps, split-adjusted. **Frozen since 2026-07-27 — no cron.** |
| `data/flow_corpus.db` → `equity_bars` | 1,505,305 daily bars, 6,058 symbols | Live to 2026-08-07. |
| EDGAR corpus + `earnings_calendar` | 124.8M facts; 28,722 calendar rows, 3,143 symbols | Live nightly. |
| `data/macro.db` | 9,248 VIXCLS observations **back to 1990**, plus credit and dollar axes | Live. Strictly better than what the `regime` dot reads. |
| `news_items` / `social_mentions` | 13 sessions / 5.4 weeks | Young but healthy. Keep collecting. |

**A year of options flow is a year of wall-clock.** No amount of effort recreates it. That asset is
worth more than every line of code in the fusion layer combined.

**What is broken is roughly: `signals/fusion/` (intake, scorer, registrar), the 15-component dot
taxonomy, `evidence_dots`, `fusion_episodes`, and the whole score→gate→reason pipeline.** That is code.
Deleting it costs an afternoon.

So *"rip it out and start over"* is the right instinct applied to the right layer. **Start over on the
interpretation. Do not start over on the data.**

---

## What each finding actually costs

### Broken, delete

| thing | measurement |
|---|---|
| `regime` + `index_pressure` | Land on **5,704 of 5,704** episodes — literal constants. φ = 1.000, they are one event written twice. **57–65% of rows display a `raw_value` that contradicts their own `detail_json`** (AAL: −1,596,218 shown vs −92,482,892 actual, off by 58×). Score impact measured at **exactly zero** — they were never scoring. |
| `equity_block` | **96.2% of its $1,266B notional is auction machinery** (48.2% official-close re-prints, 26.3% closing/opening, 15.5% contingent basket legs, 6.2% official open). Clean: 3.8%. It is a closing-auction counter. Sits in a **scoring** actor group. |
| `market_shock` | VIX arm is dead code — the literal string `"VIX"` is requested in exactly one place and **no VIX bars exist on disk** (we have VIXY, a different instrument). VIX moved **+12.4%** and **+13.5%** on 07-23 and 07-29, both far over the 8% threshold. Should have fired twice; fired zero times, ever. Plus a `MAX()` latch that never resets. |
| Direction labels | Only 4 of 15 channels carry one. Pairwise agreement **0.490 / 0.502 / 0.490 / 0.510 / 0.492** — chance is 0.500. Unanimity matches independent coin flips to three decimals. Against actual returns, 3 of 4 carry nothing; `dark_pool`'s runs **backwards** (−0.024). |
| `reddit_buzz` | Zero correlation with price at **every lag −5 to +5, including same-day.** ≥8.4% of volume is English words matched as tickers (`T` 630, `D` 451, `Q` 391, `PEG` 360, `C` 319). 66.5% collector duty cycle, one subreddit sampled per 2 hours. |

### Structurally wrong, redesign

| thing | measurement |
|---|---|
| **Absolute-dollar thresholds** | `BLOCK_MIN_PREMIUM_USD` $100k fires **954×** more in the top dollar-volume decile than the bottom; `PRESSURE_MIN_USD` $250k, **2,469×**. `ZSCORE_STD_FLOOR = 1242.65` binds on **70.8%** of name-days, silently converting a per-name z-score back into a flat dollar threshold (71.6× with it, 1.5× without). |
| **8 of 15 channels can't fire alone** | **Zero of 8,064 episodes have ever been opened by a state dot.** Social, volume, earnings, price-levels, regime can only attach to an episode a flow/tape event already created. Every state↔event correlation in the matrix is plumbing, not market structure. |
| **Additive surprisal** | Overselects **5.9× / 25.1× / 116.6×** at the 99th / 99.9th / 99.99th percentile against a permutation null with identical marginals. Within one channel across days, 3 consecutive 1-in-100 days occur **90.8×** more often than independence — naive summing overstates by 2.0 decades. |
| **Self-erasing dots** | The attention dot is a rolling state row overwritten every pass. **7 of 759 social dots carry any points.** A spike at 10:00 is gone by 15:00 — the dot structurally cannot record that something happened. |
| **The 20-day volume baseline** | `n_days` capped at 20 in `volume_profiles`, so the volume channel **cannot express anything rarer than 1-in-21, ever.** And it's `annotate_only` — it contributes nothing to scoring at all. |

### Genuinely working

- **Per-name rarity, walk-forward.** On 360,381 held-out name-days, claimed vs realised is
  **1.00× / 1.04× / 1.08× / 0.97×** at α = 0.20 / 0.10 / 0.05 / 0.01. Essentially perfect.
- **And simple beat clever, decisively.** Every parametric tail fit over-flagged by **>3×** at α=0.01
  (lognormal 3.31×, GPD 3.23×). Plain "count how many of the last 120 days were bigger" won outright.
- **The rarity transform kills the size confound.** Swapping the two absolute-$ channels for per-name
  rarity drops Spearman(cluster size, log $volume) from **+0.327 → +0.063** and variance explained from
  **24.5% → 0.8%.**
- **~9 effectively independent channels**, not 4. And the independent ones are the *small* channels —
  news, earnings, social, relative volume, price-vs-levels.
- **Persistence is real and is a distinct object**, confirmed independently in two channels. Options: 3
  consecutive top-10% days = 1-in-66, overlapping a matched single-day extreme on only **18.9%** of
  name-days. Equity: **71.7%** of 3-day runs contain no 5× day.

### ★ The correction to the founder's persistence idea

Runs are **more common** than spikes, not rarer — equity: 3-day ≥2× runs occur on **1.464%** of
name-days vs **0.378%** for a single 5× day (3.9×). So surprisal alone ranks the run *below* the spike,
the opposite of the intent.

But the reasoning behind the intuition holds, measured:

| | already moved that day | forward 5-day move |
|---|---|---|
| baseline | 1.18% | 2.82% |
| 5× spike | **8.18%** | 3.98% |
| 3-day run (no spike) | **1.98%** | 3.24% |

*"A single-day spike is usually public news already in the price"* — **measured true.** The run has
barely moved and still delivers above-baseline forward movement (+0.413pp median, 95% CI [+0.207,
+0.732]). The spike's forward edge is still ~2.9× larger in absolute terms.

**Design consequence: rarity and interestingness are two axes, and one number cannot carry both.** Every
rarity value needs an "already moved" value beside it.

---

## ★ The dividend-capture question — the founder's dispute, answered

**His objection:** *"if we stop looking at mega caps like XOM then we might not be tied down like that…
Either that or we disregard anything at the money or very very in the money."*

**On scoping by size: it does not fix it.** The 20 rarest name-days in the corpus are NVS, UMC, DB, UBS,
SIMO, CHRD, DTE, VMC and similar — pharma/bank ADRs, a utility, a materials name. The common thread is
**dividend yield, not market cap.** Deep-ITM call exercise-arb happens wherever there is a fat dividend
and liquid options; several of those names sit inside the $10B–$500B band. **Scoping the universe
doesn't target the phenomenon, because size isn't the phenomenon.**

**On his second idea — he's right, and it's better than the blunt fix.** Excluding *very deep* ITM while
keeping modestly-ITM directional flow is the more precise cut. **But `flow_rollup`'s buckets can't
express it:** the available moneyness values are `itm / atm / otm2_10 / otm10p / no_spot*`. There is no
"deep ITM" bucket. Expressing his version means re-deriving moneyness from raw prints — real work, worth
doing later, not available today.

**So the recommendation is a third framing that gets his intent with today's data: positively select
OTM rather than negatively exclude ITM.** Same effect on the dividend trade, and it aligns with the
recorded academic prior (signal concentrates in short-dated OTM). It also sidesteps the biggest
substrate gap — moneyness is unavailable for 37.8% of premium, but that gap is **entirely the
cash-settled index roots** (SPX, SPXW, NDX, RUT, VIX), which are outside a single-name universe anyway.

**One honest caveat to check before committing:** the short-dated-OTM cut's availability runs *backwards*
to the prior — available on 14.3% of name-days in the thinnest tier vs **94.3%** in the $10–100M
options-premium tier. Where the $10B–$500B band falls on that curve has not been measured.

---

## The plan

### The architectural move: delete the scoring layer, not just the bad channels

**If we never combine channels into a single number, the entire dependence problem disappears.**

No additive surprisal. No permutation null. No block-max, no copula, no whitening. The 117×
overselection, the 90.8× cross-day inflation, the "which channels are secretly the same channel"
question — every one of those exists *only because we are summing*. Stop summing and they evaporate.

The LLM reads the per-channel percentiles as a vector and reasons about the correlation itself — which
it can do, and which is exactly what was asked for: *"the dots all collect and then the LLM goes, hey,
look at all these dots."* **A composite score was never in the requirement. It was our invention, and
it is the single largest source of failure in the audit.**

### What replaces it

**One table. One row per name, per day, per statistic. Computed by counting.**

```
name_day_rarity(date, symbol, channel, statistic, raw_value, pct_rank, window_n, already_moved_pct)
```

`pct_rank` = how many of this name's last 120 days were at least this extreme. That is the whole
algorithm. It is verifiable by hand, it needs no model, and it is the one method that measured clean.

Plus a second, much smaller table of **context flags that are never scored** — earnings within N days,
news today yes/no with headlines, market regime percentile. One row per name-day and one row per day.

**v1 channels** (both have a real year of history):

| channel | statistics |
|---|---|
| **Options flow** | OTM premium (market-normalised) · largest single-leg order · 3-day persistence flag |
| **Equity price/volume** | volume · true range · gap · 3-day volume persistence |

**v1 flags** (context, zero points): earnings proximity · news today + headlines · market regime bucket.

That is ~7 numbers and 3 flags per name per day, across the ~780 names in the $10B–$500B band — of
which **770 have complete year-long history.** Eight honest numbers beats fifteen broken ones.

### ★ Sequence — SECTION BY SECTION (founder's call, 2026-08-11)

**No demolition phase and no big-bang rewrite. One section at a time, finished and verified, then the
next.** The founder's reasoning, adopted: a delete-then-rebuild plan creates a stretch where nothing
works and nothing can be inspected — and *"I can't check it myself"* is the exact condition that
produced this mess. One section is verifiable. A rewrite is not.

**Definition of done for a section** (all five, or it isn't done):

1. Reads the **right substrate** — the best one we have, not the nearest one.
2. **Wired to every surface**: the UI card, `ai/perception/resources.py`, and the agent toolsets
   (`research/book_tools.py` in-process, `tools/book_session.sh --allowedTools` headless).
3. **Measured firing rate on real data** — not "the code path is connected" but "here is what it did for
   a week." Anything at 0% or above ~20% of covered name-days is a failure, not a result.
4. **No dead ends or extras.** Orphaned constants, unreachable arms, self-erasing rows, and wires into
   the dead scorer are cut *here* — which is why no separate demolition phase is needed.
5. **One doc names it**, and the board card closes with the measured number in it.

**Section order:**

| # | section | why here |
|---|---|---|
| **1** | **Regime / market context** | Smallest, most obviously broken, nothing depends on it, and the right answer already exists on disk. Becomes the template for "done." |
| 2 | Equity price + volume | Un-freeze the 5-min store, re-base rarity off 120 days instead of 20. Biggest capability gain per unit of work. |
| 3 | Options flow | The largest channel and the best data. OTM selection + largest-single-leg + persistence. |
| 4 | News + earnings as flags | Both become context/suppressor, neither scores. Small once 1–3 set the pattern. |
| 5 | Dark pool | Parked until the daily rollup (started in §1's pass) has ~3 months of depth. |
| 6 | Social | Collect only. Fix the Reddit sampler; re-measure Jan–Feb 2027. |

**Section 1 concretely — the founder's own question was "is regime tied in with our credit spreads and
an actual VIX signal?" Measured answer: no, on both counts.** The dot reads `regime_macro` (44–57 rows,
~2 months, 4 series, **no credit axis, no dollar axis**) while `data/macro.db` holds 9,248 VIXCLS
observations back to 1990 plus `BAMLH0A0HYM2` (high-yield credit) and `DTWEXBGS` (dollar), and
`regime_report` already returns a percentile + bucket per axis. The dossier uses the good store; the dot
uses the thin one and *cannot* compute a percentile from it. Separately `market_shock`'s VIX arm
requests a symbol that has never existed on disk. Section 1 = point this at `macro.db`, make it one
day-scoped record instead of a per-name dot, wire it to all three agent surfaces, delete the dead arm
and the `MAX()` latch, and report its firing rate.

#### Section 1 — Round A SHIPPED 2026-08-11 (card #302)

**What the substrate carries now: 29 series (25 FRED + 4 Cboe), 6 report axes, all six at 100%
coverage over 250 sessions.**
Round A widened the substrate and wired it. Round B (dot deletion + dead VIX arm + latch) shipped
2026-08-13 — see below.

| axis | series | history | 250-session buckets (low/mid/high) |
|---|---|---|---|
| vol | `VIXCLS` | 9,248 since 1990-01-02 | 25 / 204 / 21 |
| **vol_term** | `VIXCLS-VXVCLS` | 4,699 since 2007-12-04 | 144 / 75 / 31 |
| credit | `BAMLH0A0HYM2` | 1,318 since 2021-07-29 | 216 / 34 / 0 |
| **funding** | `SOFR-IORB` | 1,255 since 2021-07-29 | 5 / 28 / 217 |
| curve | `DGS10-DGS2` | 12,543 since 1976-06-01 | 23 / 227 / 0 |
| dollar | `DTWEXBGS` | 5,164 since 2006-01-02 | 0 / 61 / 189 |

**On the ">20% of name-days is a failure" rule: it does not apply to this section.** Regime is a
day-scoped *context* read, not an event detector — 100% coverage is the target, and an axis at 0% would
be the failure. The rule governs Sections 2–6, where a dot is supposed to be rare.

Added, all free and keyless: **vol term structure** (`VXVCLS` deep + `VIX1D`/`VIX9D`/`VIX6M`/`VVIX` from
Cboe — no FRED equivalent at any depth, so those four are forward-only from 2026-08-11); **cross-asset
vol** (`VXNCLS`/`RVXCLS`/`OVXCLS`/`GVZCLS` — where the fear actually is, invisible to VIX);
**funding plumbing** (`SOFR`/`IORB`/`RRPONTSYD`/`WRESBAL` — the channel credit spreads miss, which is the
founder's "credit was the last war" point made measurable). 34,485 rows of deep history backfilled.

Verified reachable on **both** agent surfaces, not assumed: `tools/perception_query.py query market`
returns all 14 new fields. `_axis_history` now takes any `A-B` spread instead of
special-casing the curve, and `macro_snapshot` walks both source dicts — a series stored but absent from
the snapshot is invisible to every agent, which is the exact failure this section exists to stop.

**★ The two-question check, run on this round rather than asserted** (founder's standing rule,
2026-08-11: after *every* edit — is it wired so the AI can see it, and what dead ends are left?).
Question 1 caught two things: `macro_snapshot` walked only the FRED dict, so the four Cboe series were
**stored but invisible to every agent**; and the `market` manifest description named neither new axis,
so nothing reading `list_capabilities` would know to ask. Question 2 caught three more: an orphaned
`latest_value` import; **six series reaching the agent as bare numbers with no percentile** (the four
cross-asset vols plus the pre-existing `inflation`/`real_yield`, none of which have a `REPORT_AXES`
entry); and — the worst of them — once percentiles were attached, `VIX9D` reported **"100th percentile,
bucket high" off a sample of one**, arithmetically true and a lie in ordinary reading. `macro_snapshot`
now ranks every series against its own as-of-truncated history and reports `pctile: null` below a
60-observation floor. Final state: **29 series reach the headless agent, 25 ranked, 4 honestly
withholding a rank they cannot support, 0 bare.** Payload 6,110 bytes as the resource returns it
(the headless CLI pretty-prints, so the number against `_RESULT_CHAR_CAP = 20_000` is larger — quote
one or the other, never both as if they were the same measurement).

#### ★ Two independent Opus reviews, split mandates (2026-08-11) — 6 MAJOR, all fixed

Commissioned by the founder *because the author of the code cannot be its reviewer*. Correctness and
simplicity were given to separate agents so neither could inherit the other's blind spot. **Two claims
made to the founder were refuted:**

1. **"No lookahead" was FALSE where it mattered.** `macro_snapshot` truncated as-of; `regime_report` —
   the surface `signals/dossier.py` backdates per episode — ranked every historical session against
   the store's FULL history. Measured bucket flips: credit 2024-04-16 `mid`→`low`, funding `mid`→`high`,
   vol_term 2022-10-20 `high`→`mid`. **This is the `dossier-claim-matrix-inverted` defect in another
   costume.** Fixed; all four of the reviewer's counterexamples now reproduce its truncated column.
2. **The funding axis's stated rationale was impossible.** The comment claimed it would have caught
   Sept 2019 and March 2020; `IORB` begins **2021-07-29**, so neither event is in its window or ever
   can be. Worse, `-0.02` is the *resting* ample-reserves state and it was bucketing **"high" on 217 of
   250 sessions**. Axis kept (the gap vs its own recent history is real information), rationale
   corrected, and the manifest now tells the agent `high` here means firm-vs-recent-years, not stress.

Also fixed: the 60-obs floor guarded only `macro_snapshot`, so one payload could carry
`axes.vol.pctile=100` above `series.VIXCLS.pctile=null` — **live-reachable on any box where the backend
loop ran but the backfill did not, which is every fresh deploy since `data/` is gitignored**; a null or
garbage `last_trade_time` sliced to the literal string `'None'`, which sorts AFTER every ISO date and
so became a forward-only series' permanent "latest" row; no sanity check on the Cboe level at all
(`0.0` written straight through, and Cboe emits `0.0` placeholders elsewhere in the same payload); a
>7-day-stale store returned `None` for the whole block, **discarding 29 live series** because six axes
were stale; and the Cboe cron was the only data cron on the box with no ntfy dead-man — a silent freeze
is indistinguishable from "still accumulating history" for three months.

Flagged and NOT fixed (recorded so they are not rediscovered): the `<=` tie convention inflates ranks
on step-function series (IORB reads 38.4 with ties, 25.1 strictly-below — opposite sides of the bucket
cut; documented at `_percentile`); an intraday Cboe row carries no settle-vs-live marker until the cron
corrects it; and three axes are duplicated between the `axes` and `series` blocks (~1.1KB), which every
proposed fix made worse.

**Open, deliberately:** (1) no UI surface renders these axes — founder's call where it lands, and
**no new widget**. Cboe close-snapshot cron survived its first live firing 08-11 (checked 08-12).

#### Section 1 — Round B SHIPPED 2026-08-13 (card #302 closed)

Deleted, not retargeted. Per-name `regime` / `index_pressure` evidence-dot producers are gone
(`_attach_regime`, `_attach_index_pressure`, the day-keyed `_CONTEXT_MEMO`). Day-scoped context
already had the right readers — `regime_report` on `macro.db` via dossier `_macro_protos` and the
`market` perception resource — and stamping those facts onto every name was the shape error the
audit measured (5,704/5,704 episodes, fabricated `raw_value` on 57–65% of rows, score impact
exactly zero). Index-pressure stays a per-name dossier read for the 13 gauge underlyings only.

`market_shock`: dead VIX arm removed (`_fusion_cached_daily_move("VIX")` — no VIX bars on disk);
SQL `MAX(market_shock, ?)` replaced with assignment so the 60-min window can expire. SPY ≥2%
arm kept. Component registry still lists `regime`/`index_pressure` so leftover DB rows stay
annotate-only and cannot start scoring.

**Correction (2026-08-11): neither of the two items first listed here as "urgent leaks" is urgent, and
one of them is not ours.** Both were measured; both claims were wrong.

- **`equity_bars_5min` is a registered study store, not a production dependency, and it is not
  decaying.** Its only readers are `tools/mover_intraday.py` and `tools/mover_intraday_114g.py` —
  1.14f/1.14g. Nothing in `api/`, `signals/` or `ai/` reads it. And Alpaca serves the window
  retroactively: a live refetch of AAPL 5-min bars for 2026-08-10 (two weeks past the freeze) returned
  all 79 RTH bars. So the freeze costs nothing until Section 2 needs the trailing baseline, at which
  point one backfill run closes the whole gap. **Cron it as part of Section 2, not before.**
- **The `label_join_rate` drop is Alpaca-side, permanent, and already alarmed correctly** — see
  "Condition-label join, 08-07 onward" below. No code fix exists.
- *(Still worth starting early, and genuinely cheap: persist the daily equity off-exchange rollup,
  ~6,000 rows/day. Dark pool has no substrate at all today; three months of collection creates one.)*

#### Condition-label join, 08-07 onward — vendor outage, measured

`label_join_rate` fell from ~0.97 to **0.478 (08-07)** and **0.492 (08-10)**. Four independent
measurements say Alpaca, not us:

1. **The loss is concentrated at the open.** Captured prints by ET hour, 08-06 (healthy) vs 08-10:
   09:00 1,750k → 561k and 10:00 2,403k → 1,265k, while 12:00–14:00 are within a few percent of each
   other. A join bug would lose prints uniformly across the day; this loses the open burst only.
2. **Alpaca's own wire lag blew out.** `flow_pairing_audit` measured a-side p50 of **7,038 ms (08-07)**
   and **8,989 ms (08-10)** against a healthy ~14 ms, degraded in 65% / 75% of active minutes.
3. **It is permanent, not a consolidation lag.** Refetching the same window four days later returns
   byte-identical counts (100-contract mid-volume sample, both sessions, 0/100 differing), and 200 of
   the 37,511 contracts Databento saw trading that Alpaca returned empty are **still** empty. Re-running
   the capture recovers nothing.
4. **Detection and escalation both worked.** The audit produced four correctly-named alarms per session
   and pushed them to the ntfy dead-man topic. 2026-08-11 is the third consecutive degraded session
   (p50 20.0 s, sidecar at 49% of the spine).

Rows on these sessions are honestly mixed-provenance per Addendum F — code-labelled where the join
landed, ns-collision heuristic elsewhere — with `label_coverage` carrying the truth. **Open gap: no
consumer filters on `label_join_rate`.** Any study whose window includes 08-07 onward must exclude or
stratify on it by hand until something does.

### What the registered flow science already settles — do not re-run it

`References/FLOW_1_14F_FINDINGS_REGISTRATION.md` (212 sessions, frozen record, misses kept) **already
converges with this audit on three points, by a different method:**

- **F5 — direction is NULL** on all four components (±0.005 of coin-flip). The dependence agent
  independently measured pairwise agreement at 0.490 / 0.502 / 0.490. Two instruments, one answer.
- **F2 — `largest_print_ratio` +0.114★** (stratified +0.078★), median fired-window premium $354k. The
  options agent independently recommended largest-single-leg-order as the best second statistic (30.9%
  novel). **The same object, found twice.**
- **Caveat (e)** already states the per-name principle: *"statements about a name moving more than is
  normal FOR THAT NAME, not evidence that fired names move more in absolute terms."* And F1's retention
  0.708 vs the 0.848 placebo ("mostly RV in disguise") is this audit's size confound in another currency.

**DTE is partially answered:** F4 records that `short_dte_share` works at **day** grain and the
**intraday transplant FAILED** (+0.003, b=0/3), explicitly parked — while caveat (f) records it *does*
star on the tail-rate secondary in 9 of 9 train cells. Alive at day grain and on tail-rate, dead on the
intraday median. **Build from there; do not reopen it.**

**Moneyness is genuinely unstudied** — no ITM/OTM finding in R.1, no moneyness hypothesis in R.4. The
founder's "how far out of the money, and which DTE" question is real open ground and warrants its own
dated pre-registration under the house rule, not an ad-hoc query.

### The alternative, stated honestly

**Full start-over — new repo, new everything.** This is worse, and for exactly one reason: it discards
261 sessions of options flow and 251 sessions of 5-minute bars, and those are *wall-clock* assets. You
cannot work harder to get a year of history. Rebuilding to today's data position takes a year.

The code has no such property. Phase 1 deletes it in an afternoon.

**So the plan above is not a compromise between "fix it" and "start over."** It is starting over — on
the layer where starting over is nearly free, and keeping the layer where it isn't.

---

## ★ How to stop this from recurring

The founder's real question: *"it always seems to just be kind of done but then something's wrong or
many things are wrong or it's not wired… I don't know how we combat that."*

**What actually caught this, and what didn't.**

The tests passed the whole time. Code review would not have found it — nothing here is a bug in the
sense of wrong syntax or wrong logic. Every one of these components does exactly what it was written to
do. They were written against assumptions nobody ever checked.

What caught it was **counting what the thing actually did and comparing that to what it claimed.** A
detector firing on 93.4% of name-days is self-evidently not a rarity detector, and you need to read
zero lines of code to know that. A detector that has never fired once is self-evidently suspect. A
number displayed to the user that disagrees with its own source data is checkable in one query.

**Three changes:**

1. **A nightly channel health report.** Per channel: fire rate as a % of covered name-days, distinct
   names, last-fired date, value distribution. Auto-flag anything at 0% or above ~20%. It is a small
   amount of code and it would have caught, on day one: `market_shock` (0%), `dark_pool` (93.4%),
   `option_sweep` (~8,000 dots/day), the social dot (7 of 759 scoring), and the frozen bar store.

2. **"Done" means a measured firing rate, not a wired code path.** `CLAUDE.md` already demands wiring to
   every surface. The missing half is proving the wire carries current: *here is what it did on real
   data for a week.* No new doctrine — an acceptance step on the existing one.

3. **The question that found all of this is one the founder can always ask.** *"Show me what this
   actually did"* needs no ability to code, and the answer is always a number he can judge. Not being
   able to read the implementation was never the vulnerability. **Trusting a description of it instead
   of a measurement of it was.**

---

*Audit run 2026-08-11. Read-only — no repo file, database, or service was modified in producing it.*
