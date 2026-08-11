# Channel audit 01 — Options flow

*Agent: Opus, read-only, 2026-08-11. Substrate: `data/flow_corpus.db` → `flow_rollup`.*
*Components audited: `option_sweep`, `option_block`, `unusual_option_volume`, `directional_pressure`.*

**Independently verified by the orchestrator:** the `label_join_rate` regression (0.478 / 0.492 on
2026-08-07 / 08-10 vs ≥0.97 on 205 of 261 sessions; only 7 of 261 sessions below 0.9) and the ITM
base rate (16.5% of all 1,175,383 name-days are >80% ITM premium — the agent's 13.4% is on its
scorable-name panel; both leave the 37.5%-on-rarest-days finding standing at 2.3–2.8× enrichment).

---

## TL;DR

**This channel works as a rarity channel, and the design principle holds — but only after two
corrections that the current detector does not make.**

1. **Rarity beats multiples, decisively.** "2× its own baseline" happens on **29.3%** of name-days;
   5× on **12.7%**; 10× on **6.4%**. Fixed multiples are meaningless here. A per-name **walk-forward
   empirical rank on market-normalised premium, trailing 120 sessions** is essentially perfectly
   calibrated out-of-sample: realised/claimed = **1.00× / 1.04× / 1.08× / 0.97×** at
   α = 0.20 / 0.10 / 0.05 / 0.01, across 360,381 held-out name-days. No parametric fit needed — and
   every parametric fit tried was *worse*.
2. **The rarest days are mostly deep-ITM dividend-capture flow, not information.** **37.5%** of the
   channel's rarest days (p ≤ 0.01) are >80% ITM premium vs a **13.4%** base rate. Computing rarity on
   an ITM-excluded aggregate drops that to **1.2%** at identical calibration (1.01× at α=0.01).
   Highest-value change measured anywhere in this study; a one-line change to the aggregate.
3. **Surprisal is NOT additive across days.** Three consecutive p≤0.01 days occur **90.8× more often
   than independence**; naive −Σlog p overstates a 3-day run's rarity by **2.0 decades**.
4. **Usable range is 1-in-1.25 to 1-in-121** (max surprisal 4.80 nats / 6.92 bits), with only ~6
   resolvable levels below p=0.05. **Cross-name pooling does not honestly extend it** — measured
   per-name dispersion of the standardised q99 is 1.91 z-units, a **13× error** in claimed probability.

*Agent's own methodology note: it found and fixed a bug in its harness mid-analysis (missing name-days
were being assigned the floor p-value, inflating every calibration number). All numbers are post-fix;
earlier drafts would have been wrong by up to 3×.*

---

## 1. Substrate inventory — MEASURED

| | |
|---|---|
| `flow_rollup` rows | **89,059,737** |
| Prints / orders behind them | 2,463,166,747 / 2,310,535,400 |
| Total premium | **$11.21 T** |
| Sessions | **261**, 2025-07-28 → 2026-08-10 |
| Underlyings | 7,020 (mean **4,503**/day) |
| Name-days | 1,175,383 |
| `schema_version` | **7 for all 261 sessions** — no schema drift |

**Calendar is complete.** All 271 weekdays enumerated; the 10 missing are exactly the 10 US market
holidays. **Zero unexplained gaps.**

**Gaps that DO exist:**

- **`label_join_rate` collapse on the two most recent sessions**: 2026-08-07 = **0.478**,
  2026-08-10 = **0.492** (all other 259 sessions ≥ 0.77; 205 are ≥ 0.97). Condition-code labelling is
  broken/degraded — affects `cond` and the `multileg` flag on the freshest data. **Live regression,
  not a historical artifact.** Other low sessions: 2025-09-04 (0.770), 2025-09-10 (0.786),
  2025-09-15 (0.826), 2025-10-22 (0.847), 2026-07-31 (0.855).
- **Moneyness unavailable for 37.78% of all premium.** Every cash-settled index root (SPX $2.71T,
  SPXW $1.21T, NDXP, NDX, RUT, RUTW, VIX, XSP) lands in `no_spot_index`, plus symbology mismatches
  (BRKB, WOLF1, SATS, OPEN1). **1,137 names are >50% unbucketable.**
- **Level trend**: market daily premium $34.0bn (first 60 sessions) → $48.3bn (last 60), **1.42×**.
  Partly breadth (4,361 → 4,602 names/day), mostly per-name growth (QQQ 2.67×, SPXW 1.69×, SPY 1.45×).
- Premium is extremely concentrated: **top 10 names = 59.5%**, top 100 = **88.3%**.

**Verified repo claims:** multi-leg = **55.78%** of premium (recorded finding said 55.8% — confirmed).
Corpus is trades-level (2.46B prints), so the "daily OHLCV excludes blocks" hazard does **not** apply.

## 2. Distribution shape — MEASURED

Roughly lognormal in the middle, but fit quality and direction of tail error both **flip with
liquidity**. Per-name over 3,674 names with ≥200 days: median log-premium **σ = 1.489**; median
log-skew **+0.060**. **Anderson–Darling rejects lognormality for 55.1% of names at 5%.**

| tier (median daily premium) | n | σ_log | log-skew | p95/p50 | p99/p50 | observed p99 ÷ lognormal-predicted |
|---|---|---|---|---|---|---|
| <$10k | 1257 | 1.86 | −0.16 | 16.5 | 53.6 | **0.73** (over-predicts tail) |
| $10–100k | 1140 | 1.50 | +0.02 | 11.1 | 33.1 | 1.00 |
| $100k–1M | 739 | 1.13 | +0.30 | 7.1 | 17.6 | 1.23 |
| $1–10M | 384 | 0.90 | +0.59 | 5.0 | 12.0 | **1.41** (under-predicts) |
| $10–100M | 121 | 0.78 | +0.44 | 4.0 | 7.7 | 1.20 |
| >$100M | 33 | 0.62 | +0.44 | 2.9 | 6.5 | 1.22 |

**This kills parametric fitting.** A per-name lognormal is biased in *opposite directions* by tier.

Representative names (daily premium $, 261 days):

| name | p50 | p99 | max | p99/p50 |
|---|---|---|---|---|
| SPXW | 4,249M | 11,159M | 12,693M | 2.6 |
| SPY | 1,915M | 12,525M | 33,677M | 6.5 |
| NVDA | 1,100M | 3,469M | 5,096M | 3.2 |
| XOM | 17.8M | 226.0M | **6,179M** | 12.7 |
| CROX | 1.31M | 30.0M | 109M | 23.0 |
| LAZ | 0.041M | 0.710M | 1.45M | 17.2 |

XOM's max is **27× its own p99** — 2026-02-11, $6.18bn, ~100% deep-ITM calls, condition codes `e`/`i`.
Dividend-capture / exercise-arb, not information.

**Composition:** DTE 0-7 = 27.2%, 8-30 = 20.8%, 31-90 = 19.8%, 90+ = 32.2%. Calls 59.1% / puts 40.9%.
Multileg 55.8%. Short-dated-OTM cell (0-7 DTE ∩ OTM) = only **3.21%** of premium.

**Seasonality.** Where per-name top-1% days land (enrichment vs calendar expectation):

| | opex Friday | Thursday |
|---|---|---|
| pooled, 3,674 names | **1.13×** | **1.33×** |
| $1–10M tier | 0.95× | **1.84×** |
| >$100M tier | 0.96× | **2.07×** |

- **Monthly opex is a non-issue** (4.84% of top-1% days vs 4.29% expected; explains 0.52% of variance).
- **Thursday is the real calendar artifact**, scaling with liquidity (1.06× micro → 2.07× mega).
- **Earnings-cycle recurrence is real but secondary**: gaps between a name's own top-1% days show a
  mode at 55–72 trading days — 13.2% of gaps vs 6.5% uniform (**2.02×**).
- **Variance decomposition (median name), R² of log daily premium:** market factor 1.7%, DOW 1.1%,
  opex 0.5%, time trend 4.5%, **all together 9.6%**. For >$100M names, 30.9%.

## 3. Coverage — MEASURED

**Criterion: ≥120 observed sessions AND ≥75% active-day rate within the name's listed span.**

| threshold | names | % of names | % of premium |
|---|---|---|---|
| ≥261 days | 1,973 | 28.1% | 98.44% |
| ≥200 | 3,674 | 52.3% | 99.28% |
| ≥120 | 4,617 | 65.8% | 99.55% |
| **≥120 AND active ≥0.75** | **3,989** | **56.8%** | **99.54%** |

**3,031 names (43.2%) are unmeasurable** — but carry only **0.46%** of premium. However **248 of them
had a single order >$250k and 88 had one >$1M**: a giant print in a name with no baseline is real and
permanently unrankable. Hard limit, not a tuning choice.

**A second floor:** 1,441 of the 3,989 qualifiers have median daily premium under $10,000, and 198
under $1,000. In those names the daily total is not an aggregate at all (see §7).

## 4. Proposed normalisation → rarity — MEASURED, alternatives tested and rejected

```
x_t   = premium_usd for the name on day t, summed over buckets with mny_bucket != 'itm'
x̃_t  = x_t / (total corpus premium on day t)          # market normalisation
p̂_t  = (#{s ∈ [t-120, t-1] : x̃_s ≥ x̃_t} + 1) / (n + 1),  n = valid days, require n ≥ 100
dot   = −ln p̂_t                                        # capped at ln(121) = 4.80 nats
```

Out-of-sample calibration, 360,381 held-out name-days, realised ÷ claimed:

| estimator | α=0.20 | α=0.10 | α=0.05 | α=0.01 |
|---|---|---|---|---|
| **empirical rank, mkt-normalised, W=120** | **1.00** | **1.04** | **1.08** | **0.97** |
| empirical rank, raw (no mkt norm), W=120 | 1.08 | 1.14 | 1.18 | 1.09 |
| empirical rank, mkt-normalised, W=250 | 0.99 | 1.02 | 1.04 | 0.85 |
| empirical rank, mkt-normalised, W=60 | 0.98 | 1.03 | 1.08 | **0.00** (unreachable) |

On a fixed 180-train / 81-test split (a *stale* baseline — the naive design), at α=0.01: empirical
**1.76×**, lognormal **3.31×**, GPD peaks-over-threshold **3.23×**, upper-half lognormal **3.14×**.
**Every parametric tail model over-flags by >3×; the walk-forward window fixes the empirical one.**

Market normalisation helps in every tier and **already absorbs the Thursday effect** (adding an
explicit DOW factor changed α=0.01 calibration from 0.0097 → 0.0098, i.e. nothing).

**Where it breaks:** names with <100 valid days in the window (require the full 120); sparse
sub-channels where many days are zero (ties); newly-listed and post-corporate-action roots (WOLF1,
OPEN1, FISV/FI, CBTX, SNXX) where the window silently spans the break; and it cannot see whether the
flow is *informed* — rarity ≠ signal.

## 5. Rarity floor and ceiling — pooling tested and REJECTED

**Honest usable range: p ∈ [1/121, ~0.8]** → surprisal 0.22 to **4.80 nats (6.92 bits)**. Only **6
distinct p-values are achievable below 0.05**. Measured share of name-days at the floor: 0.737% vs
nominal 0.826% (0.89× — slightly conservative, i.e. honest).

W=250 lowers the floor to 1/251 but degrades to 0.85× at α=0.01 because the 1.42× level trend
contaminates a longer window. **W=120 is the measured optimum.**

**Pooling across names half-works, and the half that fails is the half that matters.**

- *Good news:* cross-sectional clustering is mild. Design effect vs binomial-iid = 5× at α=0.01,
  2× at α=0.001. Effective independent sample ≈ 73,000–149,000 of 362,000 pooled name-days. **Flow
  extremes are genuinely idiosyncratic, unlike returns.**
- *The killer:* names are **not exchangeable after z-scoring.** Per-name q99(z): p10 = 1.71,
  median 2.53, p90 = 3.62 — a spread of **1.91 z-units**. At the measured tail slope (λ=1.325) that
  is a **13× error** in claimed probability, ±2.6 nats against a per-name ceiling of 4.80.
- Empirically the pooled estimator is badly miscalibrated: realised/claimed 0.85× (α=0.05),
  0.76× (0.01), 0.60× (0.001), **0.25× (1e-4)**. By tier at α=0.001: 0.22× (<$10k) to 1.23× ($1–10M).

**Verdict: do not pool to extend the floor.** Report saturation honestly — "at least 1-in-121" — and
let the *count of channels* and *count of consecutive days* carry the extra information.

## 6. Persistence — the strongest structural finding

ACF of per-name demeaned log daily premium:

| lag | 1 | 2 | 3 | 5 | 10 | 20 | 40 |
|---|---|---|---|---|---|---|---|
| ACF | **0.400** | 0.314 | 0.263 | 0.219 | 0.150 | 0.102 | 0.026 |

Hyperbolic, not exponential — an AR(1) with φ=0.40 gives 0.16 at lag 2 and 0.0001 at lag 10.
**Removing the market factor changes nothing** (0.393). The persistence is name-idiosyncratic.

- "≥2× baseline for 3 consecutive days" = **8.82%** of name-days vs 2.50% iid (3.5×). But 2× is not
  rare here (29.3% of days), so this framing barely discriminates.
- "a single 5× day" = **12.69%** of name-days. **These are common events, not rare ones.**
- Rarity-matched: **3 consecutive top-20% days = 4.93% (5.9× iid, ≈1-in-20); 3 consecutive top-10%
  days = 1.51% (13.9× iid, ≈1-in-66).**
- **They are DIFFERENT objects.** A 3-day top-10% run (1-in-66) overlaps a single top-1.5% day (also
  1-in-66) on only **18.9%** of name-days.
- P(next day top-5% | today top-1%) = **0.320** vs 0.05 unconditional (6.4×); t+2 = 0.210,
  t+3 = 0.166, t+5 = 0.133, t+10 = 0.085. **Half-life ≈ 4–5 trading days; natural window 3–10 days.**

**The number to hard-code:**

| α | P(2 consecutive) | vs iid | P(3 consecutive) | vs iid | over-statement of −Σlog p |
|---|---|---|---|---|---|
| 0.20 | 0.0902 | 2.3× | 0.0525 | 6.6× | 0.8 decades |
| 0.10 | 0.0358 | 3.6× | 0.0174 | 17.4× | 1.2 decades |
| 0.05 | 0.0138 | 5.5× | 0.0055 | 44.0× | 1.6 decades |
| 0.01 | 0.00077 | 7.7× | 0.000091 | **90.8×** | **2.0 decades** |

Cross-channel, same name-day, is just as bad: total & pmax joint at α=0.01 is **39.5×** independence;
total & ITM-excluded **46.8×**; pmax & ITM-excluded **25.0×**.

## 7. Single-event vs aggregate — a distinct object, in liquid names only

Largest single order ÷ daily total, median by tier:

| tier | median conc. | days one order ≥50% | ≥90% | median orders/day |
|---|---|---|---|---|
| <$10k | **0.485** | **48.3%** | **19.1%** | 6 |
| $10–100k | 0.229 | 14.9% | 2.3% | 37 |
| $100k–1M | 0.116 | 3.9% | 0.2% | 220 |
| $1–10M | 0.063 | 1.3% | 0.0% | 1,577 |
| >$100M | 0.025 | 0.1% | 0.0% | 62,200 |

**In the <$10k tier the "daily aggregate" IS a single order half the time.** For those names a separate
`premium_max` dot is pure double-counting.

**In liquid names the object is genuinely distinct.** Spearman(rank of daily total, rank of
`premium_max`) = 0.891 median. Of 12,920 name-days where `premium_max` is top-1%: **52.3%** have a
total that is *not* top-1%, 13.2% not even top-5%.

| channel | calib α=0.05 | calib α=0.01 | fires @ p≤.01 | of which TOTAL says p>0.05 |
|---|---|---|---|---|
| total premium | 1.08× | 0.97× | 3,496 | — |
| **`premium_max` single-leg only** | **0.98×** | **0.90×** | 3,244 | **1,003 = 30.9%** |
| `premium_max` (all) | 0.99× | 0.92× | 3,326 | 537 = 16.1% |
| **short-dated OTM (0-7 DTE ∩ OTM)** | 1.06× | 0.87× | 736 | **327 = 44.4%** |
| OTM ≥10% | 1.12× | 0.97× | 3,260 | 854 = 26.2% |
| contracts (not $) | 0.96× | 0.89× | 3,195 | 465 = 14.6% |
| ITM-excluded premium | 1.07× | **1.01×** | 3,599 | 315 = 8.8% |
| multileg premium | 1.01× | 0.75× | 2,022 | 180 = 8.9% |
| single-leg premium | 1.08× | 0.98× | 3,537 | 114 = 3.2% |

**`premium_max` restricted to single-leg prints is the best second dot** — 30.9% novel and better
calibrated than the multileg-inclusive version (multileg legs are priced by the package allocator, so
a huge "single order" there is often just one leg of a spread).

## 8. Verdict

**Yes — two dots plus a persistence flag. But the current detector is not that channel.**

**What the live detector does today:** ~**13,000 dots/day** across ~**950 symbols** — roughly 13.7 dots
per flagged name per day, `points` = 0.0 on all 164,920 rows. Top emitters are NVDA (9,779 in 3 weeks),
SPCX, TSLA, MU, AAPL — it fires in proportion to liquidity, not unusualness. A firehose with no
per-name normalisation.

**Priority order for change:**

1. **Exclude ITM from the rarity aggregate.** Premium-weighted ITM share climbs monotonically with
   rarity — 37.7% on ordinary days → **68.1%** on the rarest. 13 of the 20 rarest name-days are ≥79%
   ITM: NVS 2026-03-10 ($894M, 99.9% ITM), UMC, DB, UBS, SIMO, CHRD, DTE, VMC — ADR/high-dividend
   names on ex-div dates doing deep-ITM call exercise-arb.
2. **Add `premium_max` (single-leg) as a second, separate dot** — 30.9% novel at p≤0.01. Suppress it
   for names in the <$10k tier.
3. **Add a persistence flag** (3 consecutive top-10% days ≈ 1-in-66, 18.9% overlap with a matched
   single-day extreme). **Do not implement it by summing daily surprisals.**
4. **Hard-code the non-independence.** Never sum −log p across consecutive days (90.8× at α=0.01) or
   across this channel's own sub-dots (25–47×). Hand the LLM *one* dot per name-day plus explicit
   "day 2 of an elevated run" metadata.
5. **Fix the `label_join_rate` regression** on 2026-08-07 / 08-10.

**Fundamentally not measurable here:**

- Anything rarer than **1-in-121** per name. Pooling gives a conservative bound, not a probability.
- **Whether the flow is informed.** This channel measures *how much* and *how concentrated*, never
  *why*. Correct dot semantics: "something unusual in the options tape here", never "someone is buying".
- **Direction at daily grain.** Multileg is 55.78% of premium and flips the session sign of
  `net_premium`. No directional dot from this table.
- **Moneyness for 37.8% of premium** — every index root. The short-dated-OTM cut is available on only
  **39.4%** of name-days, and availability runs *backwards* to the academic prior: 14.3% of name-days
  in the <$10k tier vs **94.3%** in $10–100M. The prior says signal is in less-liquid names; the
  corpus can only cut that way in liquid ones. **Unresolved tension, reported not papered over.**
- **3,031 names (43.2%)** have no measurable normal — including 88 that saw a single order over $1M.
