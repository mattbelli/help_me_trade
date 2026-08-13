# Channel audit 02 — Dark pool + equity blocks

*Agent: Opus, read-only, 2026-08-11. Substrate: `data/trading.db` → `equity_tape_events`.*
*Components audited: `dark_pool` (97,816 dots), `equity_block` (28,082 dots).*

**Independently verified by the orchestrator:** `EVENTS_PRUNE_DAYS = 30` at `db/equity_tape.py:19`;
the 22-session span; and the condition-code decomposition of `equity_block` notional —
**48.2% official-close `M` + 26.3% closing/opening `6/5` + 15.5% contingent `7/V` + 6.2% official-open
`O/Q` = 96.2% artifact, 3.8% clean.** (Agent reported 96.6%; the small gap is bucket-assignment order.)

---

## PUNCHLINE

**The raw prints are not retained. This channel has no substrate deeper than a 30-day, already-
thresholded event log — and the current dot fires on 93.4% of covered name-days, so as a "something
unusual" signal it carries 0.068 nats.** Two further structural findings: `equity_block` is **96.6%
auction/dissemination artifact by notional** and should not survive as a channel in its present form;
and the tick-test direction is worthless (47.8% buy, correlation with same-day return **−0.024**,
wrong sign).

The rarity redesign **does work here, better than expected** — normalize to share-of-dollar-volume and
the distribution is near-identical across a 22× ADV range and essentially lognormal with a *thinner*-
than-Gaussian tail; pooled surprisal is well calibrated to p≈0.01. But **the phenomenon you care about
most — sustained multi-day accumulation — is not measurable**: day-to-day autocorrelation of log share
is +0.06 against volume's +0.46.

---

## 1. Substrate inventory

**Raw prints are NOT persisted.** `signals/equity_stream/stream.py` subscribes Alpaca SIP
`trades["*"]` (~11k symbols); `runner.py:_on_trade` hands each print to `TapeDetector.on_print` and
**only survivors are written.**

| Store | What | Depth | Names |
|---|---|---|---|
| `data/trading.db` `equity_tape_events` | thresholded large prints | **2026-07-13 → 08-11 (22 sessions)**, `EVENTS_PRUNE_DAYS=30` | 2,686 dp / 2,223 blk |
| `data/flow_corpus.db` `equity_bars` | daily OHLCV, **total volume only** | 2025-07-25 → 2026-08-07, 1,505,305 rows | 6,058 |
| `data/equity_bars_5min/*.npz` | 5-min RTH bars, **total volume only** | 2025-07-28 → 2026-07-27, 251 days | ~6,412 |
| `data/trading.db` `daily_bars` / `momentum_daily_bars` | daily OHLCV | 111 / 503 names | — |

Counts: `dark_pool` 149,023 events / $1,084B; `equity_block` 54,887 / $1,266B.

**Gaps that matter:**

- **No off-exchange volume share exists anywhere on disk.** `signals/finra.py` is *short interest*
  only — the FINRA OTC-transparency / ATS weekly files are not ingested. `data/alpaca_conditions`
  (19G) and `data/gth_tape` are **OPRA options**, not equity. Detected prints are median **3.6%** of a
  name's dollar volume against a real-world off-exchange share of ~40–50%, i.e. **we see roughly 8% of
  actual off-exchange flow.**
- **60s cooldown censors the busiest names.** `signals/equity_tape.py:31` allows one event per
  (symbol, kind) per minute. MSFT RTH-minute occupancy mean 0.272, peak 0.489 (191 of 390 minutes).
  Daily notional for mega-caps is a **censored sample, not a total.**
- **The threshold is non-stationary for reasons unrelated to the market.**
  `load_profile(max_age_days=7.0)` — a stale volume profile drops the symbol to the `$10M` no-profile
  floor:

| session | events | % at the $10M no-profile floor | symbols **with** a fresh profile |
|---|---|---|---|
| 07-13 | 2,168 | 0.0% | **112** |
| 07-17 | 9,489 | 10.9% | **875** |
| 08-07 | 13,120 | 0.5% | **1,795** |
| 08-10 | 11,853 | 0.6% | **1,831** |

The measured universe **tripled** (112 → 886 → 1,831) purely from profile-cache warming. **Any
baseline fit across this window is fitting a cache refresh schedule.**

- **The "1bp of ADV" scaling is inert.** 83.6% of events fired at the flat `$1M` floor; only **11
  symbols ever** got an ADV-scaled floor (AAPL, AMD, INTC, MSFT, MU, NVDA, QQQ, SNDK, SPCX, SPY, TSLA).
  In practice this is a **flat $1M global threshold.**

**What the detector currently fires on** (fixed 691-name × 17-session panel):
`P(≥1 dark_pool dot | covered name-day) = 0.934`, mean 9.2 dots/name-day; `P(≥1 equity_block) = 0.798`.
Self-information of the binary dot: **0.068 nats.**

## 2. Distribution shape

**Per-print notional** heavy-tailed in raw dollars — dark_pool p50 $1.92M / p90 $12.3M / p99 $95.6M /
max $3.09B; block p50 $3.93M / p99 $276M / max $2.39B.

**But the raw scale is meaningless — condition codes explain why.**

| | dark_pool ($1,084B) | equity_block ($1,266B) |
|---|---|---|
| `M` Market Center **Official Close** (*a re-print of the close, not a trade*) | 0% | **48.3%** |
| `6/5` closing/opening print | 0% | 26.3% |
| `O/Q` official open | 0% | 6.2% |
| `T/U` Form-T late-reported | 41.7% | 0.8% |
| `P` prior-reference-price | 21.0% | 0% |
| `7/V` contingent / QCT (basket legs) | 18.0% | 15.4% |
| `W/B` **average-price report** (a whole day's VWAP as one print) | 17.2% | 0% |
| **CLEAN (no artifact code)** | **37.3%** | **3.4%** |

The 16:10 / 18:30 / 19:00 / 20:00 ET clusters (4,478 / 4,131 / 3,954 / 1,420 events) are **100%
condition `M`** — the same closing auction re-disseminated up to four hours later. SPY 2026-07-17
books its close *twice*: the auction, then a 2,572,040-share `M` print at 20:00 ET. **`equity_block` is
largely a closing-auction counter.**

**Scaling with ADV — dollars are meaningless, share fixes it completely.**

| tier | median ADV | daily dp$ p50 | **dp share p50** | share p90 | share p99 | max-print/ADV p50 |
|---|---|---|---|---|---|---|
| Q1 | $135M | $5.9M | **0.0381** | 0.1482 | 0.3286 | 0.0256 |
| Q2 | $280M | $11.6M | **0.0392** | 0.1402 | 0.3144 | 0.0179 |
| Q3 | $489M | $21.5M | **0.0410** | 0.1399 | 0.3227 | 0.0161 |
| Q4 | $1,114M | $49.8M | **0.0443** | 0.1345 | 0.3083 | 0.0153 |
| Q5 | $2,931M | $157.5M | **0.0388** | 0.1265 | 0.2658 | 0.0116 |

**Raw notional spans 27×; share spans 1.16×.** Note max-print/ADV does *not* pool — it drifts 2.2×
(0.0256 → 0.0116), so single-print size needs per-tier handling that share does not.

**Tail shape (share):** skew −0.12, excess kurtosis −0.13; standardized quantiles 1.30 / 1.63 / 2.15 /
2.65 vs Gaussian 1.28 / 1.64 / 2.33 / 3.09. **Not heavy-tailed — lognormal with a thinner-than-normal
tail in logs.** Mechanism (inferred): share is bounded above by the true off-exchange fraction.
Measured: P(share>0.3)=0.013, P(>0.5)=0.0012, max 0.646.

**Seasonality — the prior is half wrong**, measured on the 1-year substrate (5,647 names × 256
sessions, mean z of log dollar volume):

- **Quarterly rebalance / triple-witching Friday: z = +0.816** (n=16,918) — market-wide.
- Russell recon 2026-06-26 was the single biggest session, z = +1.12.
- Monthly opex Friday: **+0.052**. Month-end last session: **−0.016**. Day-of-week: **±0.03**.

**Month-end is a non-event; quarterly rebalance is a ~0.8σ market-wide event — and our 22-session tape
window contains zero of them.** Any rarity model fit on this window will emit a market-wide dot storm
on 2026-09-18.

## 3. Coverage

Criterion: **≥10 sessions carrying ≥1 dark_pool print** — at T=10 the per-name offset's standard error
(0.27 nats) is still below the across-name offset spread (0.483 nats), so the offset is identifiable.

Over 2026-07-16 → 08-11 (17 sessions), against 6,047 symbols with bars:

- 2,498 have any tape event
- **1,405** ≥5 sessions · **927** ≥10 · **601** ≥15 · 506 ≥16
- 234 appear but have zero dark_pool days
- **5,120 of 6,047 (85%) are unmeasurable** for a per-name normal.

Coverage is tier-dependent: `P(≥1 dp print/day)` = 0.826 (Q1 thin) → 0.983 (Q5).

## 4. Proposed normalization → rarity

**Statistic:** `s = Σ(dark_pool print notional) / (close × volume)` for the ET session, dark_pool only,
artifact codes `W B 7 V T U P 4` dropped. Do **not** use raw notional, do not use `equity_block` as-is,
do not use direction.

**Estimator: pooled lognormal with a shrunk per-name offset.** Measured decomposition of `log s`:

- pooled μ = −3.222, σ = 0.962 (geometric-mean share 0.0399)
- per-name offset sd τ = 0.483 nats → **name identity explains 25% of variance**
- per-name residual sd median 0.842 ≈ pooled 0.962 → **the shape is common; only the level differs**
- at T=17, SE of a name's offset = 0.204 → **shrinkage λ = τ²/(τ²+SE²) = 0.85**

`z = (log s − (μ_pooled + λ·offset_i)) / σ_pooled`, `p = 1 − Φ(z)`, `surprisal = −ln p`.

**Calibration (measured — genuinely good):**

| nominal p | expected | actual |
|---|---|---|
| [0, 0.01) | 0.010 | **0.004** |
| [0.01, 0.05) | 0.040 | 0.044 |
| [0.05, 0.10) | 0.050 | 0.055 |
| [0.10, 0.25) | 0.150 | 0.150 |
| [0.25, 0.50) | 0.250 | 0.250 |

**Where it breaks:** below p≈0.01 the lognormal *overstates* rarity (fitted share at p=0.001 is 0.779
vs empirical 0.512, ratio 1.52) — cap it, don't extrapolate. Zero-inflation for thin names (17.4% of
Q1 name-days have no print at all). And it breaks completely on quarterly-rebalance days, which the
fit window has never seen.

## 5. Rarity floor and ceiling

- **Per-name empirical: T=17 usable sessions → finest resolvable p = 1/17 = 0.059 → ceiling 2.83 nats.**
  The 30-day prune caps this at 1/21 = 0.048 (3.04 nats) **permanently** — history is deleted, so this
  ceiling never improves under current retention.
- **Pooled: n=10,827 symbol-days → honest floor p ≈ 0.005 (≈5.3 nats)**, set by where calibration
  departs, not by n.
- **The statistic is bounded, so surprisal saturates**: max observed share 0.646; P(share>0.5)=0.0012 →
  **~6.7 nats is the hard ceiling this channel can ever emit.**
- **Pooling is sound — the strongest positive result here.** KS D vs Q5 is 0.048–0.079 across all
  tiers; mean log-share moves only 0.0379 → 0.0438 across a 22× ADV range. **ADV normalization does
  make names comparable.**

## 6. Persistence — kills the headline use case

Bias-corrected within-name autocorrelation, stable-universe window 07-16 → 07-31:

| series | lag1 raw | **lag1 corrected** | lag2 | lag3 |
|---|---|---|---|---|
| log dollar volume *(control)* | +0.361 | **+0.456** | +0.159 | −0.031 |
| log dp count | +0.100 | **+0.196** | +0.070 | −0.029 |
| log dp notional | +0.014 | **+0.110** | +0.046 | −0.036 |
| **log dp share** | −0.038 | **+0.058** | +0.014 | −0.023 |
| log max single print | −0.033 | +0.062 | +0.037 | −0.028 |

Over the full window dp count reads +0.328 — **that is the profile-cache level shift, not persistence**;
it halves to +0.196 once the universe is stable.

**The 3-day run test** (z of log share ≥1.0, p=0.108): **25 runs of ≥3 consecutive days vs 13.1
expected under independence (1.9×)**, against **863 isolated single days**. Controls: dp count 107 vs
46.5 (2.30×), **dollar volume 109 vs 38.5 (2.83×)** — the block channel is *less* persistent than the
volume channel it would sit beside.

**Natural window:** intraday AM/PM split-half within-name reliability is **r=0.229** for notional
(Spearman-Brown full-day 0.373) — **63% of a day's deviation is draw noise.** Disattenuating the
next-day autocorrelation (0.110 / 0.373) gives a latent daily state autocorrelation of **≈0.30** —
real but below our measurement floor. A **3-day rolling mean** is the shortest window with usable
reliability; a 1-day dot is mostly noise. **Sustained accumulation is not measurable with the data we
retain — a data-retention verdict, not a market verdict.**

## 7. Single event vs aggregate

**Single events are not lost — the daily aggregate essentially *is* a single-print statistic.**
Max-print / daily-sum: p10 0.207, p25 0.319, **p50 0.494**, p75 0.727, p90 1.000. **20.6% of
symbol-days have one print ≥80% of the day's dark-pool notional; 14.1% have exactly one print.**

Aggregating loses little — but `max_usd` and `sum_usd` are near-duplicates and should not be two dots.

## 8. Verdict

**Redundancy test — NOT a proxy for total volume, but the independent part is mostly noise.**
Cross-sectional log-log r = 0.683 (conflates size — ignore). **Within-name, demeaned: r = 0.481,
R² = 0.231** — volume explains only 23% of dark-pool notional variance. For `equity_block`, R² = 0.026.
Share CV within name is 0.80 at the median. **So: not redundant.** But split-half reliability caps the
real content at ~37% of the residual; most of the remaining 77% is sampling noise from a censored,
thresholded draw process.

**Direction is worthless and must be dropped.** All 203,910 events carry `direction_basis='tick'`.
Tick-test buy fraction of determined prints = **0.4781**. corr(daily signed imbalance, same-day
open→close return) = **−0.0235**; next-day = **−0.0055**. Same-day return by imbalance quintile:
**+20.1, +17.6, +14.0, +13.3, +1.9 bp** — monotonically *backwards*. Mechanically expected: a TRF print
is reported up to ~10s late and 17.2% of dark-pool notional is an average-price report whose price is a
VWAP unrelated to the current tick.

**Is it worth a dot? Yes — one dot, radically narrower than today's.**

- **Keep `dark_pool`**, as a *same-day* rarity dot on ADV-normalized share, direction-free,
  artifact-codes filtered. At p≤0.05 it would fire on ~2.5% of covered name-days (3.0 nats) instead of
  93.4% (0.068 nats).
- **Do not ship `equity_block` in its current form.** Filtered to clean codes it collapses to $42.8B
  (3.4%), 905 prints/day, median $1.54M, and only **78 names** retain ≥12 sessions — below the coverage
  bar. It is a closing-auction counter wearing a block-print label.
- **Do not claim multi-day accumulation.** Honest statement is "a big off-exchange day", never
  "sustained accumulation".

**Three retention/hygiene changes are prerequisites, not polish:**

1. `EVENTS_PRUNE_DAYS=30` permanently caps per-name rarity at p=0.048 — a compact daily rollup
   (symbol, date, share, count, max, clean-code split) is ~2,500 rows/day and would give real depth.
2. The `max_age_days=7` profile expiry makes the threshold a function of a cache refresh — pin the
   floor to the ADV table, not the profile cache.
3. The window has never seen a quarterly rebalance (z=+0.82 market-wide) — the rarity model needs an
   explicit calendar term or it will storm on 2026-09-18.
