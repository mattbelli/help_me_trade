# Channel audit 05 — Social (StockTwits + Reddit)

*Agent: Opus, read-only, 2026-08-11. Substrate: `data/trading.db` → `social_mentions`.*
*Components audited: `stocktwits_buzz` (474 dots), `reddit_buzz` (282 dots).*

**Independently verified by the orchestrator:** `signals/social_buzz.py:33` uses
`https://api.stocktwits.com/api/2/streams/trending.json` — the **trending** endpoint, a fixed-size feed;
the ET-hour profile is dead flat (3,123 at 06:00 to 4,118 at 17:00 across all 24 hours, including
03:00–05:00); and **7 of 759 social dots carry any points** (`stocktwits_buzz` 3/476, `reddit_buzz`
4/283).

---

## PUNCHLINE

**StockTwits buzz is real signal about *news*, but it is signal you already have, and mostly already
priced.** The founder's premise — "retail piles in after it already ran over days" — is **wrong for
this data**: buzz spikes are *no more* likely than a random day to follow a ≥2σ multi-day run (8.2% vs
11.2% base rate). What buzz spikes actually track is **today's news event**: ≥2σ overnight **gap** 16×
enriched, earnings-reaction day 9× enriched. **By the time the buzz is visible pre-open, the move is
already in the gap.** The genuinely-early case (calm tape, no gap, then a ≥2σ intraday move) is **3.7%
of pre-open spikes — 19 events in 24 sessions**, and its lift is not statistically establishable with 5
weeks (day-clustered bootstrap CI crosses zero).

**Reddit as currently collected is noise.** Zero correlation with |return| at every lag from −5 to +5
including lag 0 (mean r = −0.022 contemporaneous), ≥8.4% of its volume is english-word ticker false
positives (`T`,`D`,`Q`,`PEG`,`C`), 66% collector duty cycle, only 337 names.

**Verdict: keep collecting, don't score it yet.** Kill the reddit dot now; demote StockTwits to an
unscored *context* dot until ~6 months of history exists (≈ Jan–Feb 2027).

---

## 1. Substrate inventory

`social_mentions(symbol, bucket, source, mentions)`, PK `(symbol,bucket,source)`. Bucket = **ET hour**
label `YYYY-MM-DDTHH`. Rows only exist where `mentions > 0` (`db/social_mentions.py:78` skips
`amount <= 0`) — **absent hours are not stored as zeros.**

| source | rows | mentions | symbols | ET dates | span |
|---|---|---|---|---|---|
| stocktwits | 35,138 | 87,514 | 2,264 | 39 | 2026-07-04T22 → 2026-08-11T14 |
| reddit | 17,446 | 33,346 | 337 | 39 | 2026-07-04T23 → 2026-08-11T14 |
| **union** | 52,584 | 120,860 | **2,347** | 39 (26 weekday + 11 weekend) | **5.4 weeks** |

**Universe composition:** of 2,264 StockTwits names, 1,459 match `flow_corpus.equity_bars`, **277 are
crypto** (`.X` suffix, **12.3% of all ST mentions**), and 528 are unpriceable OTC/foreign (`AABB`,
`ADMQ`, `360ONE.NSE`, `ABX.TSX`…). **~23% of the ST channel is not a US equity we can trade.**

**★ Producer findings that dominate everything below:**

- **StockTwits is the *trending* endpoint only** (`api/2/streams/trending.json`, line 33), polled every
  30 min. Mentions/day = **2,325 ± 321 (CV 0.138)** and the hour-of-day profile is **flat across all 24
  ET hours** (84–112 mentions/hr, including 03:00–05:00 ET). Real chatter is not flat at 4am. **We are
  measuring a fixed-size feed at a fixed poll rate, not chatter volume.** A per-name "spike" is a
  *share-of-a-constant-pie* move — a name can only rise if others fall.
- **Reddit is 1 subreddit per 30-min cycle, round-robin over 4 subs** (line 196–211) → each sub sampled
  every ~2h, `new.rss?limit=100`. Duty cycle: reddit rows present in **622 of 936 (66.5%)** day-hour
  cells. Outage days: 07-25 (15 mentions), 08-05 (84), 08-10 (18). Symbol extraction = cashtags
  (unrestricted) + bare uppercase words matched against watchlists ∪ S&P500 with a 49-word blacklist →
  measured contamination `T`:630, `D`:451, `Q`:391, `PEG`:360, `C`:319 = **≥8.4% of reddit volume.**

**Current rule** (`signals/fusion/intake.py:429-467` → `db/social_mentions.py:195`):
`ratio = mentions_this_ET_hour / mean(mentions in same day-part over trailing 14d, excluding today)`,
requiring ≥3 distinct days present. Gate `FUSION_SOCIAL_SPIKE_RATIO = 2.0`; points `= min(3, log2(ratio))`.

**Replayed over the whole substrate:** ST fires on 2,846 of 19,331 ready hour-cells = **14.7%, ≈77
fires/day**; reddit 781/11,194 = **7.0%, ≈21/day**. ST ratio quantiles p50=0.75, p90=2.43, p95=3.60,
p99=8.00. **45% of nonzero ST hour-cells can't even compute a ratio** (<3 baseline days).

**Why the live dot counts look nothing like 77/day:** the dot is `refresh_state=True`, one rolling row
per `(episode, source)`, overwritten every pass. **Of 474 + 282 dots, exactly 2 have `points > 0`
(orchestrator's independent recount: 7).** A spike at 10:00 is erased by the 15:00 refresh — **the dot
cannot record that a spike happened.**

## 2. Distribution shape

StockTwits — **84.4% of all symbol-days are zero:**

| bucket | n names | zero% | p50 | p75 | p90 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|---|
| mega ≥$2B/day | 65 | 28.3 | 2 | 11 | 48 | 111 | 245 | 374 |
| large $200M–2B | 420 | 78.7 | 0 | 0 | 2 | 5 | 21 | 259 |
| mid $20–200M | 541 | 86.7 | 0 | 0 | 1 | 2 | 14 | 243 |
| small <$20M | 400 | 92.8 | 0 | 0 | 0 | 1 | 3 | 90 |

Reddit — 68.8% zero overall over a pre-selected 337-name universe.

**A single global rule is proven meaningless:** a small-cap's **p99 is 3 mentions**, below a mega-cap's
p50-to-p75 range.

**Day of week:** ST mentions/day is *identical* weekday vs weekend (2,301 vs 2,384) but **distinct
names/day jumps 293 → 478.** Same fixed pie over 63% more names → per-name counts structurally lower
and more diffuse on weekends. Reddit goes the other way (951 → 760 mentions/day, names flat).
**Weekend must be a separate baseline stratum, and Monday's pre-open window contains ~64h of chatter vs
~17h** — the agent had to weekday-match the baseline or Monday manufactured the spikes.

## 3. Coverage

| ≥ nonzero days | stocktwits | reddit |
|---|---|---|
| ≥5 | 711 | 249 |
| ≥10 | **461** | **145** |
| ≥15 | 292 | 93 |
| ≥20 | 187 | 62 |
| all 37 | 15 | 2 |

**Criterion: ≥10 nonzero days.** With ≥10 the relative SE of μ under a Poisson-ish count is ≲32%,
propagating to <1 quantile step on the pooled curve; below 10 the μ estimate swings by >50%.
**~461 StockTwits names and ~145 Reddit names — ~500 scorable of 2,347 (21%).**

## 4. Per-name normalization → rarity

Naive per-name empirical rank is dead on arrival: with 30 of 35 days at zero, `P(X ≥ x)` has 5 usable
support points and the maximum is `P = 1/37 = 0.027`.

**The measured escape hatch: the standardized tail shape is stable across size buckets.** For names
with ≥10 nonzero days, μ = mean daily mentions (zeros included), then `P(X ≥ k·μ)`:

| bucket (ST) | n | P(X≥2μ) med | P(X≥3μ) | P(X≥5μ) | P(X≥10μ) |
|---|---|---|---|---|---|
| mega | 61 | 0.135 | 0.054 | 0.027 | 0.000 |
| large | 160 | 0.135 | 0.081 | 0.027 | 0.000 |
| mid | 125 | 0.162 | 0.108 | 0.054 | 0.000 |
| small | 40 | 0.162 | 0.108 | 0.054 | 0.000 |

**Medians vary by ≤2× across buckets spanning 100× in market cap.** So: **per-name μ (the only thing
fit per name) × a POOLED tail curve.** Pooled `u = x/μ` over 461 ST names: p50=0.13, p75=1.21, p90=2.85,
p95=4.46, p99=9.25, p99.9=17.84.

**Where it breaks:**

1. **μ is a moving target.** Because ST is a fixed-size trending feed, μ for a name that entered the
   rotation two weeks ago is meaningless. 45% of ST hour-cells are still `warming` after 5 weeks.
2. **Zeros carry no information under this collector.** A zero could mean "nobody talked about it" or
   "the trending endpoint didn't surface it" or (reddit) "we were 429'd". **~1/3 of reddit's zeros are
   our outage, not silence.**
3. **The pooled name-days are not independent.** 17,057 name-days come from 37 calendar days with heavy
   market-wide clustering — the 158 calm spike-events came from only **18 distinct sessions.**

## 5. Measurable rarity floor and ceiling

- **Per-name: floor 1-in-37 (4.5 bits), and do not trust below 2-in-37.** Every bucket's `P(X≥10μ)`
  median is **0.000** — already below resolution. Anything claiming "1-in-200 for this name" is
  fabricated.
- **Pooled: p99.9 = 17.8×μ is reachable**, ~10 bits, *if* you accept exchangeability within a bucket.
  Given the clustering, cap the honest claim at **~1-in-500 (≈9 bits)** and never quote a per-name
  rarity beyond 1-in-37.
- **Pooling is the only thing that makes this channel scoreable at all**, and it is empirically
  justified by the bucket-invariant tail. But it buys *tail resolution*, not *history*. **Use
  hierarchical shrinkage, gate on ≥10 nonzero days, and treat the output as display-only for now.**

## 6. Persistence — YES, distinguishable

Spike = `x ≥ 5` and `x ≥ 3× trailing-10-day mean`:

| | episodes | 1-day only | ≥2 days | run 3 | run 4 | day-after still ≥1.5× baseline |
|---|---|---|---|---|---|---|
| stocktwits | 865 | 682 (**79%**) | 183 (**21%**) | 21 | 3 | **55%** (n=1,053) |
| reddit | 225 | 151 (**67%**) | 74 (**33%**) | 25 | 6 | **82%** (n=336) |

**Five weeks is enough to separate the two.** Sustained-vs-one-day is a usable, measurable distinction
today — **the one thing in this channel to trust at 5 weeks.** (SPCX ran 168/205/302 on 07-22/23/24 and
293/368/246/292 on 08-04..08-07 — genuine multi-day episodes.)

## 7. ★ Reactive vs anticipatory — the measurement

Prices: `flow_corpus.db → equity_bars`, 24 usable sessions 2026-07-07→2026-08-07. Each ET hour-bucket
assigned to: **ON** = 16:00 prior session → 09:00 (weekends roll into Monday), **ID** = 09:00–15:59,
**PC** = 16:00–23:59. σ = that name's own daily-return stdev.

**(a) Lead–lag correlation, per symbol, averaged (n=372 ST / 146 reddit):**

| | ON→\|ret\| **prior** day | ON→\|ret\| **same** day | ID→\|ret\| same day | PC→\|ret\| same day | ON+ID→\|ret\| **next** day | PC→**signed** ret next day |
|---|---|---|---|---|---|---|
| stocktwits | +0.063 | **+0.137** | **+0.330** | +0.172 | +0.035 | −0.010 |
| reddit | −0.040 | −0.018 | −0.030 | +0.033 | +0.004 | −0.033 |

StockTwits is **concurrent-dominant**, decaying to ~0 for the next day. **No directional (signed)
information at any horizon. Reddit is flat zero everywhere including lag 0.**

**(b) The spike-level split.** Spike applied to the **pre-open-only** series so every spike is strictly
observable before the open:

| | ST pre-open spikes (n=517) | ST all-day (n=587) | **CONTROL (n=8,533)** | reddit pre-open (n=210) |
|---|---|---|---|---|
| **POST-move** — prior 1/3/5d ≥2σ | **12.2%** | 8.2% | **11.2%** | 11.4% |
| **POST-news gap** — ≥2σ overnight gap, no prior run | **12.6%** | 11.1% | **0.7%** | 1.0% |
| **CONCURRENT** — open→close ≥2σ only | **3.7%** | 5.8% | **1.5%** | 2.4% |
| **QUIET** — nothing ≥2σ | **71.6%** | 75.0% | **86.7%** | 85.2% |

**This inverts the founder's premise.** Buzz spikes have **no lift at all** on "already ran over days"
(12.2% vs 11.2% = 1.09×). What they are enriched for is **the overnight gap: 16–18× lift** (weekday-
matched: 18.4% vs 0.8% = 23×). **The gap is fully realized at the open.**

**(c) Dose–response confirms a real mechanism** (u = day mentions / name's mean):

| u_pre-open | n | prior 3d ≥2σ | **gap ≥2σ** | open→close ≥2σ | mean signed open→close |
|---|---|---|---|---|---|
| [0,1) | 7,314 | 4.7% | 0.5% | 1.8% | −0.01% |
| [1,2) | 706 | 5.7% | 1.6% | 2.3% | +0.32% |
| [3,5) | 411 | 4.4% | 7.3% | 4.4% | −0.02% |
| [5,9) | 254 | 7.9% | 13.4% | 4.7% | +0.06% |
| **[9,∞)** | **85** | 7.1% | **25.9%** | **12.9%** | **+1.15%** |

Gap risk rises monotonically 0.5% → 25.9%; prior-3-day is **flat** at 4–8%. **Buzz intensity tracks
today's news, not last week's move.**

**(d) What the buzz actually is: earnings.** **20.7% of ST pre-open buzz spikes land on an
earnings-reaction session vs a 2.3% base rate = 9× lift**, and **64% of the ≥2σ gappers among spike
days are earnings-driven** (lower bound). We already have that calendar on disk, days in advance, free.

**(e) The one genuinely anticipatory case, honestly.** Strip out prior movers AND gappers: **158
truly-calm pre-open spikes, from only 18 distinct sessions, 129 distinct names.** Same-day open→close:
5.1% hit ≥2σ vs 1.8% for calm controls (2.8× lift); mean +0.66% vs −0.05%. **Day-clustered bootstrap
(4,000 resamples over sessions) on the mean difference: point +0.71%, CI95 [−0.45%, +1.66%]. Crosses
zero. Neither confirmed nor refuted by 5 weeks.** Anyone quoting the +0.66% as an edge is quoting 18
days of session noise.

**Platform break-out:** ST has a measurable (if late) news signal; reddit's spike split is
statistically indistinguishable from its control column on every row.

## 8. Verdict

**Not worth a scored dot today.**

- **Reddit — stop scoring now, and fix the collector before collecting more.** Not thin data, *broken*
  data: zero correlation at every lag, ≥8.4% english-word contamination, 66.5% duty cycle, 337 names.
  More history does not fix a censored, contaminated sampler. Fixes: drop bare-ticker matching to
  cashtags-only, sample all 4 subs per cycle with proper backoff, persist the dedupe set across restarts.
- **StockTwits — keep collecting, demote to unscored context.** Two things it can honestly say today:
  (i) **"multi-day sustained chatter"** vs one-day print (21% vs 79%, 55% day-after persistence) — real
  and needs no price validation; (ii) **"this name is in a news event today"** — real (16× gap lift, 9×
  earnings lift) but ~redundant with the earnings calendar and news feed we already run, and *late*.
  **It should not carry surprisal points while the only validated content is "there's news".**
- **Also fix regardless of history:** the rolling `refresh_state=True` attention dot *overwrites* the
  spike. If dots are to accumulate as evidence, a spike needs an **immutable event dot keyed on the ET
  hour it fired.** And any per-name normalization must stratify weekend vs weekday and
  Monday-vs-other pre-open windows.

**How much history, and by when.** Two independent requirements:

1. **Per-name rarity resolution.** ~50 trading days per name ≈ 2.5 months → **mid-Sept 2026** for the
   ~187 names with near-daily coverage; the long tail never gets there and must use the pooled curve.
2. **Powering the calm-tape edge test — the binding constraint.** Detecting the observed 1.5% → 3.7%
   lift at 80% power needs ~816 calm spike-events per arm ≈ 38 sessions under independence. But events
   cluster hard (158 events from 18 sessions), so a design effect of 3–8× is realistic → **115–300
   trading sessions ≈ 6–14 months.** Collection started 2026-07-04: **first genuinely powered read
   around Jan–Feb 2027, comfortable mid-2027.** *(Inferred — the design effect is an assumption, not a
   measurement.)*
