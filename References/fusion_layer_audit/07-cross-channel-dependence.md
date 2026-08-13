# Channel audit 07 — Cross-channel dependence

*Agent: Opus, read-only, 2026-08-11. The load-bearing measurement: which channels are secretly the same
channel, and how much of the "cluster of dots" is really "this is a big name".*

**Independently verified by the orchestrator:** **zero of 8,064 episodes have ever been opened by a
state dot** (0 state-only / 1,765 event-only / 6,299 both); and the absolute-dollar thresholds
`BLOCK_MIN_PREMIUM_USD = 100_000`, `PRESSURE_MIN_USD = 250_000`, `ZSCORE_STD_FLOOR = 1242.65` in
`signals/options_flow/constants.py`.

---

## PUNCHLINE

1. **The "cluster of dots" is today ~59% a restatement of "this is a big name."** Spearman(log
   dollar-volume, number of distinct channels firing) = **0.701**; 20 dollar-volume bins explain
   **59.4%** of the variance in cluster size. Conditioning on within-day dollar-volume vigintile
   destroys a **median 56%** of the log-odds of cross-channel co-firing, and **92–100%** of it for every
   `dark_pool` pairing.
2. **The cause is specific and fixable: absolute-dollar thresholds.** On 251 sessions of substrate, an
   absolute-$ detector fires **954×–2,469×** more often in the top dollar-volume decile than the bottom;
   the same statistic thresholded at the *name's own p90* fires **1.8×** more. Swapping the two
   absolute-$ channels for per-name rarity drops Spearman(cluster size, log$vol) from **+0.327 →
   +0.063** and variance-explained from **24.5% → 0.8%**. **The target design's per-name rarity
   transform *is* the fix — this is the measurement that says so.**
3. **`regime` and `index_pressure` are not two channels, and not one channel — they are a market-wide
   broadcast stamped on every episode.** φ = 1.000, 5,707/5,707 rows pair on identical `(symbol, ts)`.
   **Zero per-name information; exactly 0 independent dimensions.**
4. **Naive additive surprisal overselects by 5.9× / 25× / 117×** at the 99th / 99.9th / 99.99th
   percentile against a within-name permutation null with identical marginals. Block-max only gets that
   to 2.1× / 7.5× / 32.5×.

---

## 1. The shipped co-occurrence matrix

**Window** `2026-08-03 .. 2026-08-07` (5 trading days — the only window where all 15 channels were live
*and* daily bars exist). **Universe** = every symbol-day with a daily bar, **N = 29,542.** (A real
denominator, not the dot-conditioned one — dot-conditioned universes create Berkson selection and flip
signs.)

Firing rate (% of 29,542 symbol-days): `dark_pool` 19.5, `option_sweep` 15.9, `news` 9.7,
`equity_block` 9.4, `index_pressure` 6.6, `regime` 6.6, `session_level` 6.0, `option_block` 5.5,
`rvol_ex_prints` 3.4, `earnings_proximity` 1.35, `unusual_option_volume` 0.76, `directional_pressure`
0.44, `stocktwits_buzz` 0.42, `reddit_buzz` 0.31, `price_alert` 0.00.

**φ matrix** (mean-square contingency; `price_alert` omitted):

```
                        swp    dp   eqb   opb  news   uov  idxp   reg   lvl  rvol  earn   stw  dirp   red
option_sweep           1.00  0.46  0.50  0.46  0.48  0.20  0.32  0.32  0.30  0.21  0.15  0.14  0.15  0.10
dark_pool              0.46  1.00  0.52  0.32  0.40  0.17  0.20  0.20  0.20  0.19  0.16  0.09  0.13  0.08
equity_block           0.50  0.52  1.00  0.43  0.42  0.25  0.08  0.08  0.07  0.09  0.15  0.11  0.19  0.10
option_block           0.46  0.32  0.43  1.00  0.38  0.30  0.14  0.14  0.13  0.11  0.06  0.15  0.25  0.12
news                   0.48  0.40  0.42  0.38  1.00  0.22  0.17  0.17  0.13  0.14  0.13  0.12  0.18  0.08
unusual_option_volume  0.20  0.17  0.25  0.30  0.22  1.00  0.01  0.01  0.02  0.01  0.03  0.10  0.72  0.11
index_pressure         0.32  0.20  0.08  0.14  0.17  0.01  1.00  1.00  0.77  0.24  0.09  0.09  0.01  0.07
regime                 0.32  0.20  0.08  0.14  0.17  0.01  1.00  1.00  0.77  0.24  0.09  0.09  0.01  0.07
session_level          0.30  0.20  0.07  0.13  0.13  0.02  0.77  0.77  1.00  0.21  0.09  0.07  0.01  0.06
rvol_ex_prints         0.21  0.19  0.09  0.11  0.14  0.01  0.24  0.24  0.21  1.00  0.19  0.14  0.01  0.11
earnings_proximity     0.15  0.16  0.15  0.06  0.13  0.03  0.09  0.09  0.09  0.19  1.00  0.02  0.04  0.05
stocktwits_buzz        0.14  0.09  0.11  0.15  0.12  0.10  0.09  0.09  0.07  0.14  0.02  1.00  0.08  0.26
directional_pressure   0.15  0.13  0.19  0.25  0.18  0.72  0.01  0.01  0.01  0.01  0.04  0.08  1.00  0.08
reddit_buzz            0.10  0.08  0.10  0.12  0.08  0.11  0.07  0.07  0.06  0.11  0.05  0.26  0.08  1.00
```

φ **understates** dependence here — low-base-rate binaries have a marginal ceiling. `φ/φ_max` exposes
near-subset relations:

| pair | φ | φ_max | φ/φ_max | containment |
|---|---|---|---|---|
| option_sweep ⊃ unusual_option_volume | 0.200 | 0.201 | **0.995** | P(sweep \| uov) = **0.989** |
| option_sweep ⊃ directional_pressure | 0.151 | 0.153 | **0.991** | P(sweep \| dirp) = **0.993** |
| unusual_option_volume ⊃ directional_pressure | 0.724 | 0.759 | 0.953 | P(uov \| dirp) = **0.961** |
| option_sweep ⊃ stocktwits_buzz | 0.145 | 0.149 | 0.971 | P(sweep \| stw) = **0.958** |
| index_pressure ≡ regime | 1.000 | — | 1.000 | 1.000 |

### ★ The structural confound nobody can see from the matrix

```sql
WITH e AS (SELECT episode_id,
   sum(dot_kind='event') nev, sum(dot_kind='state') nst
   FROM evidence_dots GROUP BY 1)
SELECT sum(nev=0 AND nst>0) only_state, sum(nev>0 AND nst=0) only_event,
       sum(nev>0 AND nst>0) both, count(*) FROM e;
-- (0, 1765, 6284, 8049)
```

**Zero of 8,049 episodes were opened by a state dot.** All 8 state channels (`regime`,
`index_pressure`, `session_level`, `rvol_ex_prints`, `earnings_proximity`, `stocktwits_buzz`,
`reddit_buzz`, `price_alert`) are *attached* by `intake_once` to an episode that a tape/flow **event**
already created. **Every state↔event φ in the matrix above is mechanically induced, not empirical.** And
`regime`/`index_pressure` stamp **97.1%** of eligible episodes.

**In the target design ("each channel INDEPENDENTLY answers…"), 8 of 15 channels currently cannot emit a
dot at all without another channel's permission. That is the biggest single gap between shipped and
target.**

## 2. ★ Which channels are secretly the same channel

**(a) Literally the same thing, double-inserted**

- **`regime` ≡ `index_pressure`** — φ=1.000, perfect 1:1 on `(symbol, ts)` and `episode_id`. Both are
  market-wide constants replicated per name (regime = 1–2 values/day across up to 1,185 symbols;
  index_pressure = 16–39 values/day). `raw_value` and `ts` also freeze at first write. **Not channels.
  Make them one episode-level context field with zero surprisal weight.**

**(b) Two detectors on one underlying phenomenon** — all four options channels read the *same day's
option prints for the same underlying*:

- **`unusual_option_volume` + `directional_pressure`** — φ=0.72 shipped; on 251 sessions
  `option_block`+`directional_pressure` gives **φ=0.70, OR=87** (adj. OR 20–22 after volume
  conditioning). `P(uov | dirp)=0.961`. **Same object.**
- **`option_sweep` ⊃ {`unusual_option_volume`, `directional_pressure`}** — 98.9% and 99.3% containment.
  Year-deep within-name rarity: `optblock~dirp` **0.67**, `optblock~uov` **0.53**, `uov~dirp` **0.42**.
  Only `option_sweep`'s *split-execution* statistic is partly distinct (ρ 0.21–0.30).
- **`session_level` + `regime`/`index_pressure`** — φ=0.77. **Pure stamping architecture**, not market
  structure: both attach in the same `context_done` block.
- **`rvol_ex_prints` + the event channels** — φ 0.09–0.24, but `_detected_prints_shares`
  (intake.py:811) literally subtracts `equity_tape_events` block/dark-pool shares from the rvol
  numerator. **It is defined against two other channels.**

**(c) Genuinely distinct, merely correlated**

- `stocktwits_buzz` + `reddit_buzz` (φ=0.26, lift 41) — two crowds, correlated but not one. On the
  substrate their within-name rarity correlation is only **0.12**.
- `news` + everything (φ 0.38–0.48 shipped) — but after volume conditioning `dark_pool+news` OR goes
  **12.6 → 1.05.** News is distinct; its shipped correlation is liquidity.
- `session_level` vs any move/volatility channel — **negatively** dependent (within-name ρ = **−0.53**;
  reconstructed `LVL+BIGMOVE` φ = −0.55). Big moves take price *away* from yesterday's levels.
  **The most complementary pair in the system.**

**(d) A latent bug worth naming.** `ZSCORE_STD_FLOOR = 1242.65`
(`signals/options_flow/constants.py:19`) binds on **70.8%** of name-days. It converts UV-2's per-name
z-score into a level threshold: top/bottom dollar-volume decile firing ratio **71.6× with the floor,
1.5× without.** **`unusual_option_volume` is not currently a per-name channel.**

## 3. ★ Continuous-substrate dependence (261 sessions, no threshold)

Panel: `flow_rollup` aggregated to (date, underlying) — 1,175,383 rows — joined to `equity_bars`
(1.5M rows; **the year of daily bars is already on disk, no Alpaca fetch needed**). Final panel
**1,027,628 name-days / 251 sessions / 4,829 names.**

Spearman on within-name percentile ranks — **exactly the design's proposed rarity transform**:

```
              sweep  optblk    uov   dirp   rvol  level   move  $vol*
opt_sweep      1.00    0.21   0.30   0.23   0.10  -0.06   0.07   0.13
opt_block      0.21    1.00   0.53   0.67   0.19  -0.11   0.13   0.32
opt_uov        0.30    0.53   1.00   0.42   0.32  -0.18   0.22   0.22
opt_dirp       0.23    0.67   0.42   1.00   0.17  -0.11   0.13   0.29
eq_rvol        0.10    0.19   0.32   0.17   1.00  -0.24   0.30   0.72
eq_level      -0.06   -0.11  -0.18  -0.11  -0.24   1.00  -0.53  -0.19
eq_move        0.07    0.13   0.22   0.13   0.30  -0.53   1.00   0.23
eq_$vol*       0.13    0.32   0.22   0.29   0.72  -0.19   0.23   1.00
```

\* `$vol` is the **stand-in** for `dark_pool`+`equity_block` — see limits.

Adding news/social/earnings on the 3-week overlap (58,532 name-days): all cross-family within-name
ρ ≤ 0.30. `reddit` is ≤0.05 with everything.

Average-linkage clustering on 1−|ρ|, cut at |ρ|≥0.35, gives **four blocks**:

```
[option_block, option_uov, option_dirp]   [option_sweep]   [rvol, $vol]   [level, move]
```

**Pooled (cross-sectional, no per-name normalization) the picture is far worse** —
`opt_premium~opt_blockmax` **0.96**, `opt_premium~opt_dirnet` **0.92**, `opt_premium~eq_dollarvol`
**0.69.** That gap between pooled 0.96 and within-name 0.86 is the size effect again.

## 4. ★ The volume confound — the most important result

**Shipped dots, 5 sessions, N=29,542:**

| log $-volume vigintile | median $vol | mean # channels firing | P(k≥4) | P(k≥6) |
|---|---|---|---|---|
| 0 | $0.03M | 0.001 | 0.000 | 0.000 |
| 10 | $18.5M | 0.292 | 0.016 | 0.000 |
| 15 | $97.2M | 1.271 | 0.087 | 0.006 |
| 19 | **$1,082M** | **4.488** | **0.723** | **0.210** |

- Spearman(log$vol, k) = **0.701**; Var(k) explained = **59.4%.** rvol explains **2.0%**.
- Median **56.4%** of the log-OR is destroyed by 20-stratum dollar-volume conditioning; conditioning on
  *rvol* destroys **~2%**.
- Using the name's **trailing-20d baseline** volume (known before the day starts) gives Spearman 0.684
  and 58.6% — **it is a name-size effect, not a busy-day effect.**

Pairs that dissolve completely:

| pair | OR | OR adj. $vol | % log-OR killed |
|---|---|---|---|
| dark_pool + option_sweep | 12.44 | **0.69** | >100 (flips negative) |
| dark_pool + option_block | 13.28 | **0.81** | >100 (flips negative) |
| dark_pool + news | 12.57 | **1.05** | 98 |
| dark_pool + equity_block | 35.60 | **1.35** | 92 |
| equity_block + news | 15.63 | 1.65 | 82 |
| option_sweep + equity_block | 24.30 | 1.73 | 83 |

Pairs that **survive** (real, not liquidity): `uov+dirp` 147, `option_sweep+option_block` 8.2,
`option_sweep+session_level` 4.9, `stocktwits+reddit` 18.7, `option_sweep+news` 3.4,
`option_sweep+rvol_ex_prints` 3.0.

**Year-deep confirmation of the mechanism** (251 sessions, shipped thresholds reconstructed):

| threshold style | firing rate, top vs bottom $vol decile | Spearman(fire, log$vol) |
|---|---|---|
| absolute $ — `BLOCK_MIN_PREMIUM_USD` $100k | **954×** | +0.455 |
| absolute $ — `PRESSURE_MIN_USD` $250k | **2,469×** | +0.429 |
| absolute $ — sweep premium floor $25k | 31.5× | +0.593 |
| z-score with `ZSCORE_STD_FLOOR` (as shipped) | 71.6× | +0.104 |
| z-score, no std floor | 1.5× | +0.028 |
| `CONV_RVOL_MIN` 1.5 (per-name ratio) | 1.4× | +0.043 |
| ATR-scaled level proximity | 0.9× | −0.047 |
| **per-name p90 rarity (the redesign)** | **1.8×** | **+0.053** |

Swap the two absolute-$ channels for per-name p90 rarity: Spearman(k, log$vol) **+0.327 → +0.063**,
Var explained **24.5% → 0.8%**.

**Residual, honest:** even fully per-name-normalized, the additive score retains a small tilt —
top-5,000 name-days have median within-day $-volume percentile **0.709** vs 0.500 baseline (Spearman
+0.123). Tested whether that's because dependence is stronger in liquid names: **it is not** — mean |ρ|
is 0.259–0.271 and effective rank 5.87–6.00 across all five liquidity quintiles, flat. **The residual
comes from news/social/earnings intrinsically concentrating in big names** (P(news) rises 0.012 → 0.431
across volume deciles, Spearman +0.325), **which is a fact about the world, not a threshold artifact.**

## 5. How many effectively independent channels

**Blunt answer: about 9 of the 15 — not 15, and not 4.**

| set | dim | eff. rank (entropy) | participation | PCs for 90% |
|---|---|---|---|---|
| 8 substrate channels, within-name rarity, **251 sessions** | 8 | **5.95** | 4.77 | 6 |
| same, dropping the $vol stand-in | 7 | 5.56 | 4.58 | 6 |
| 12 substrate channels (+news, stocktwits, reddit, earnings), 3-week overlap | 12 | **9.24** | 7.46 | 9 |
| 11, dropping the $vol stand-in | 11 | 9.27 | 7.77 | 9 |

Method: exp(Shannon entropy of the normalized eigenvalue spectrum) of the Spearman correlation matrix —
the continuous analogue of "how many equally-weighted independent directions", and unlike a 90%-variance
PC count it doesn't jump on an arbitrary cutoff.

**Check that changed nothing:** the 12-channel 9.24 might be inflated because 3-week within-name ranks
use only ~13 observations. Control — the same 8 year-deep channels re-ranked *inside* the 3-week window
give eff_rank **5.64** vs 5.95 year-deep, i.e. slightly *lower*. **The short window does not inflate.
9.2 is real, and news/social/earnings genuinely add near-independent dimensions.**

Ledger against the 15 shipped:

- `regime`, `index_pressure`: **0 dimensions.** `price_alert`: ~0.
- The four options channels: **≈2** (block/uov/dirp collapse to ~1; sweep's split statistic adds ~1).
- `dark_pool`+`equity_block`: **≤1 measured**, and unmeasurable properly (see limits).
- `rvol_ex_prints`, `session_level`, `news`, `earnings_proximity`, `stocktwits_buzz`, `reddit_buzz`:
  **~5–6, the most independent things in the system.**

## 6. Is additive surprisal salvageable?

Test: u = rank/(n+1) within name, S = Σ −ln(1−uᵢ) over 8 channels, **972,577 name-days / 250 sessions.**
Null = shuffle each channel's surprisal independently **within each name** — preserves every marginal
exactly, destroys only cross-channel dependence. (Stronger than a Gamma(k,1) null, which would have
confounded discreteness with dependence.)

| percentile | independence cutoff (nats) | observed quantile | **overselection** |
|---|---|---|---|
| 99% | 15.25 | 21.37 | **5.9×** |
| 99.9% | 18.21 | 28.17 | **25.1×** |
| 99.99% | 20.86 | 32.43 | **116.6×** |

Block-max over the 3 measured blocks: **2.1× / 7.5× / 32.5×.** Per-block: options block alone
overselects 5.1×/18.0×; equity-volume pair 3.8×/13.6×; the price-structure pair **under**selects
(0.15×/0.03×) because `level` and `move` are *negatively* dependent — a uniform shrink would be wrong.

**Recommendation — one approach: keep the additive sum, but calibrate it empirically against a
within-name permutation null.** Compute the panel nightly (it already exists for options + equity: 251
sessions on disk), shuffle each channel within name, and report the cluster's rarity as its quantile in
that null distribution rather than as nats.

Why this and not the alternatives:

- **Block-max** is a partial fix that still misstates the extreme tail by ~33×, and it discards the
  second-largest dot in a block — exactly the corroboration the product wants to show the LLM.
- **Gaussian copula / whitening** works statistically but **ranks almost disjointly**:
  Spearman(S_additive, χ²_whitened) = **0.256**, top-1,000 overlap = **0.2%**. The additive top-5,000
  has every channel near its own p95 ("everything was big at once"); the whitened top-5,000 has channels
  at middling percentiles in *combinations the correlation structure says shouldn't co-occur*. **These
  are two different products.** The whitened lens is arguably the better "connect the dots" object and
  worth building — as a **second** lens, not as the correction to the first.

**Costs:** (a) the score is no longer interpretable as nats — the honest statement becomes "top 0.01% of
composite days for this name"; (b) every channel needs a maintained per-name history panel, which
**`dark_pool` and `equity_block` do not have**; (c) the null must be recomputed whenever a channel is
added, retuned, or its threshold changes — a scoring-weight-diff-grade event.

## 7. Direction coherence

Only **4 of 15** channels carry a non-`none` direction: `option_sweep`, `option_block`, `dark_pool`,
`equity_block`. Everything else is `direction='none'`, `points=0.0`.

| pair | n | agreement |
|---|---|---|
| dark_pool vs option_sweep | 5,188 | **0.490** |
| dark_pool vs equity_block | 4,375 | 0.502 |
| equity_block vs option_sweep | 3,274 | 0.490 |
| dark_pool vs option_block | 1,161 | 0.510 |
| equity_block vs option_block | 942 | 0.492 |
| **option_block vs option_sweep** | 1,392 | **0.561** |

Unanimity on multi-channel name-days matches independent coin flips to three decimals: k=2 observed
**0.493** vs chance 0.500; k=3 **0.253** vs 0.251; k=4 **0.129** vs 0.126. **Split-direction clusters
are 62.3% of all ≥2-channel days.**

**Why** — ground truth against the daily bar:

| channel | Spearman(signed imbalance, same-day return) | sign-hit | next-day |
|---|---|---|---|
| dark_pool | **−0.013** (p=0.18) | 0.490 | −0.016 |
| equity_block | +0.005 (p=0.71) | 0.495 | −0.029 |
| option_block | +0.009 (p=0.74) | 0.506 | +0.005 |
| **option_sweep** | **+0.073** (p=7e-12) | **0.527** | +0.027 (p=0.015), 0.510 |

**The tick-test directions on off-exchange and block equity prints carry no information about even the
same-day return.** Only `option_sweep`'s quote-rule direction does, weakly. The one pair whose agreement
*rises* with magnitude is `option_block`↔`option_sweep` (0.553 → 0.625 → **0.658** above the 80th
percentile) — same tape, same quote rule.

**"The dots disagree on direction" is currently not a market fact; it is 3 of 4 direction fields being
coin flips. Fix the labels before designing around the disagreement.**

## 8. Honest limits

**Could not measure at all:**

- **`dark_pool` and `equity_block` have no continuous substrate.** `equity_tape_events` holds only
  post-threshold detections and starts 2026-07-13. The full SIP tape is never persisted; there is no
  equity equivalent of `data/research/print_lab/`. Daily dollar volume was used as a stand-in for both,
  conflating them into one dimension. **Fix: persist one row per symbol-day of off-exchange (exchange
  code `D`) share volume + notional, and total block-eligible notional. ~6,000 rows/day. Three months
  gives a real substrate; a vendor backfill would give it immediately.**
- **`option_sweep`'s true detector** clusters same-contract prints within 500ms across ≥3 exchanges
  (`signals/options_flow/sweeps.py:_cluster_event`). `flow_rollup` is aggregated past that; prints/orders
  was used as a split-execution proxy. `data/research/print_lab/<date>.jsonl` has the raw options prints
  and could reconstruct it — ~1 day of work.
- **Whether any of this predicts outcomes.** Not asked, not measured.

**Window-constrained:**

- The shipped picture rests on **5–6 trading days.** Every shipped number is provisional; the
  251-session substrate reconstruction is what to bet on. Where the two disagree (shipped 56% log-OR
  killed by volume vs reconstructed 32%), the difference is explained: the shipped system has ~5
  absolute-$ event channels of 15 plus 8 state channels that can only attach to episodes those opened,
  so it is *more* volume-confounded than the 6-channel reconstruction.
- `news_items` ~3 weeks (plus 958 stale-timestamp rows, excluded by every window filter).
  `social_mentions` 5 weeks, reddit only 337 symbols.
- `earnings_proximity` approximated as −|calendar days to nearest `earnings_calendar` date|, ignoring
  the `estimated` flag.

**Method caveats:**

- ET trading day = UTC−4 throughout. Overnight GTH prints land on the prior ET date.
- `equity_bars` in `flow_corpus.db` is split-adjusted only; dollar volume = close × volume.
- The `regime`/`index_pressure` "shrink" figure in the volume-confound table is meaningless for that
  pair (perfect concordance + continuity correction); read the φ=1.000 instead.
