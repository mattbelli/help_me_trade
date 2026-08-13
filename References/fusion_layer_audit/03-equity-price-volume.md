# Channel audit 03 — Equity price + volume

*Agent: Opus, read-only, 2026-08-11.*
*Components audited: `rvol_ex_prints` (2,694 dots), `session_level` (5,649), `price_alert` (4).*

**Independently verified by the orchestrator:** `data/equity_bars_5min/` contains 503 files, **every one
written 2026-07-30**, newest data 2026-03-10.json, no crontab entry references it — the store is a
one-shot backfill, frozen. And `ANNOTATE_ONLY_GROUPS = frozenset({"background_crowd", "context"})` at
`signals/fusion/scorer.py:16` with `background_crowd = frozenset({"rvol_ex_prints"})` — the volume
channel contributes nothing to scoring.

---

## Punchline

**The channel is worth a dot, and the substrate is ~25× better than assumed — but it is frozen, and
the current live detector scores literally zero.** Three findings dominate:

1. **`data/equity_bars_5min/` is the real substrate**: 251 sessions × ~5,900 symbols of 5-minute SIP
   OHLCV, split-adjusted, **1,482,456 symbol-days**. Not 111 names — **5,289 names with a complete
   trading year**, including **770 of 781** names in the $10B–$500B band. But it is a **one-shot
   backfill run 2026-07-30, with no cron**, frozen at 2026-07-27.
2. **The persistence hypothesis is half right, and the half that's wrong breaks the surprisal design.**
   A 3-day ≥2× run is a genuinely *distinct* object (71.7% contain no 5× day) — but it is **3.9×
   MORE common** than a single 5× day (1.464% vs 0.378%), so pure surprisal weighting ranks it *lower*
   (6.09 vs 8.05 bits), the opposite of the intent.
3. **Rarity must be empirical-rank, never a fitted lognormal.** At a claimed p=0.01 the lognormal fit
   realizes **2.74%** — over-claims rarity by 2.7×. Empirical rank realizes **1.00%**. And the live
   detector's baseline (`n_days=20`) caps honest rarity at **1-in-21 = 4.4 bits**, forever.

---

## 1. Substrate inventory

| Store | Names | Depth | Content | Status |
|---|---|---|---|---|
| `data/equity_bars_5min/*.npz` | **6,323** (5,877–5,938/session) | **251 sessions**, 2025-07-28→2026-07-27, zero gaps | 5-min OHLCV, RTH-only, 78 buckets, SIP, split-adjusted | ⛔ **FROZEN** — one-shot, no cron |
| `daily_bars` (trading.db) | **111** | 583 days, 2024-04-12→2026-08-10 | full OHLCV | live (yesterday) |
| `momentum_daily_bars` | 503 | 362 days | **open+close ONLY** — no volume, no high/low | useless here |
| `data/volume_profiles/*.json` | 2,730 | **`n_days` capped at 20** (2,676 of 2,730) | `cumfrac` (78) + `median_daily_total` | live |
| `fusion_day_volume` | 2,546 | **`PRIMARY KEY symbol`** → 1 row/symbol | a **cache, not history** | live |
| `fusion_z_baseline` | 1,305 | **6 days** (2026-07-16→07-21) | peak/close z | ~3 weeks stale |
| `edgar_facts.db` + `fundamentals.db` | 10,433 sym→CIK | 124.8M facts | shares outstanding | live (nightly) |

- **Intraday volume SHAPE** — 2,730 names, but only a **20-day** baseline.
- **Daily volume LEVEL history** — **6,323 names × 251 days**, derivable from the 5-min store.
- **Price/range history** — same store, full high/low/open/close.

**Stored vs one call away:** everything needed is *already stored*; the gap is **freshness, not
existence**. Re-running `tools/equity_bars_5min_backfill.py` is ~167 requests/session at a measured
55.8s/session. So: "we don't store it" is false; "**we stopped updating it**" is true.

## 2. Distribution shape (target band: 765 names × 191 days; pooled liquid set = 852,815)

| Quantity | raw skew | raw ex-kurt | **log** skew | **log** ex-kurt | verdict |
|---|---|---|---|---|---|
| Volume / trailing-60 median | +165.0 | +39,458 | **+0.88** | **+3.71** | lognormal-*ish*, tractable in logs |
| True range / trailing-60 median | +14.9 | +1,021 | **+0.38** | **+1.54** | **best behaved** ✅ |
| Gap (open vs prev close) | +450.0 | **+301,704** | — | — | pathological *as reported* |

- Range is better behaved than volume — **confirmed, measured.**
- Neither log is actually normal (D'Agostino K² p≈0 at n=200k). Lognormal is an approximation.
- **The gap moments are an artifact of 73 name-days out of 852,815 (0.0086%).** Dropping them:
  skew +450→**+2.1**, ex-kurt +301,704→**+103**, sd 4.00%→**1.97%**. The reverse-split hypothesis was
  **refuted** — median(price_ratio × volume_ratio) = **6.19**, not 1.0, and only 4.3% have clean integer
  ratios. These are real microcap events (BYND's Oct-2025 squeeze at 787× volume is in there).

**Seasonality** (mean log-ratio):

- **Day of week**: Thu highest (vol ×1.100, TR ×1.124); Fri lowest (×1.041). ~6% volume swing.
- **Opex**: 3rd-Friday ×1.137 vs other Fridays ×1.016 → **+12% volume, +10% range.** Worth a control.
- **Holiday-shortened week**: ×0.995 vs ×1.079 full week → **−8%**.
- **Month-of-year / earnings season: NOT IDENTIFIED.** One year of data, so month effects are perfectly
  confounded with that month's regime. Worse, the 60-day burn-in **deletes Aug/Sep entirely.**
  Do not use it.

## 3. Coverage

**Criterion: 251/251 sessions present AND median daily dollar volume ≥ $1M.** The $1M floor removes
denominator collapse — the top pooled volume "outliers" include SMX at **1005× against a 60-day median
of 80 shares**, which is arithmetic, not information.

- 5,289 / 6,323 names have all 251 sessions; **4,465 pass both criteria.**
- **Target band $10B–$500B: 781 names identified, 770 (98.6%) with the full year, 765 pass both.**
  Median of their median daily dollar volume: **$161M. Coverage in the founder's band is effectively
  complete.**
- **Unmeasurability correlates strongly with market cap:** nano-cap 5.8% incomplete history / 56.4%
  below $1M, vs target band **1.4% / 0.8%**.
- Market cap resolves for 3,281 / 6,323 panel names; the unresolved are ETFs and foreign filers with no
  `dei` cover-page tag. **Caveat:** `dei:EntityCommonStockSharesOutstanding` is per share class, so
  multi-class CIKs (GOOG/GOOGL) get the count duplicated.

## 4. Proposed normalization → rarity

**Use per-name empirical rank on a trailing window that excludes today. Do not fit a lognormal.**

Measured calibration (W=120, today excluded — the `closes[-1]` trap respected throughout):

| claimed p ≤ | lognormal fit realizes | empirical rank realizes |
|---|---|---|
| 0.10 | 0.1194 (1.19×) | 0.1206 (1.21×) |
| 0.05 | 0.0717 (1.43×) | 0.0616 (1.23×) |
| 0.02 | 0.0400 (**2.00×**) | 0.0204 (**1.02×**) |
| 0.01 | 0.0274 (**2.74×**) | 0.0100 (**1.00×**) |
| 0.005 | 0.0192 (3.83×) | 0.0000 (**below floor**) |

The parametric fit fails precisely in the tail you want to alert on. Empirical rank is calibrated to
1.00× right down to its floor, then goes to zero — **an honest, visible failure mode rather than a
silent 2.7× inflation.**

**Drift is real and requires the trailing window:** last-60d median ÷ first-60d median across names is
p25=0.92, **median 1.11**, p75=1.39; **25.8% of names moved their normal by >1.5× within the year, 9.0%
by >2×.** A fixed full-history baseline would be wrong for a quarter of names. W=60–180 all handle it;
W=180 calibrates best (1.01× at p=0.05) but costs burn-in and adapts slower.

## 5. Rarity floor and ceiling

The floor is **1/(W+1)**:

| baseline | floor p | max surprisal |
|---|---|---|
| **live `cumfrac`, n_days=20** | **1/21 = 4.8%** | **4.4 bits** |
| trailing 60 | 1.6% | 5.9 bits |
| trailing 120 | 0.83% | 6.9 bits |
| full year (251) | 0.40% | 8.0 bits |

**The live detector is structurally incapable of expressing anything rarer than 1-in-21. That is the
single highest-leverage fix in this channel.**

**Pooling across names is NOT sound for thresholds:** one global threshold at the pooled 99th
percentile gives per-name fire rates p5=0.00% / median 0.52% / p95=3.14% against a 1% target. Not
Poisson noise — observed 28.5% of names never fire vs **14.8% expected** (95% band 12.4–17.4%),
**overdispersion factor 2.82×.** Per-name normalization is mandatory.

Pooling for *tail-shape* estimation is more defensible (per-name sd varies only **1.66×** p5→p95) but
that is a shrinkage model, a fitted claim not a measured one.

## 6. Persistence — the central hypothesis

**Target band $10B–$500B, measured:**

| event | frequency | surprisal |
|---|---|---|
| single day ≥5× | **0.378%** (1 in 264) | **8.05 bits** |
| 3 consecutive days ≥2× | **1.464%** (1 in 68) | **6.09 bits** |

**The 3-day run is 3.9× MORE common than a 5× spike, not rarer.** Volume elevation clusters hard — the
run beats its iid null (0.0352%) by **41.6×**; 5 consecutive days by 2,989×. The clustering is real and
large, but it makes runs *common*, not *rare*.

**It IS a distinct object**: **71.7% of 3-day runs contain no ≥5× day** (66% of names show a run; only
39% show a spike).

**The mechanism claim is confirmed in direction** (medians; baseline = all days):

| event | same-day \|ret\| | prior-3d \|ret\| | **forward-5d \|ret\|** | forward vol-ratio |
|---|---|---|---|---|
| baseline | 1.18% | 2.13% | 2.82% | 1.05 |
| 5× spike | **8.18%** | 11.01% | **3.98%** | 2.25 |
| 3-day run (no 5× day) | **1.98%** | 4.47% | **3.24%** | 1.81 |

The spike has already moved 8.18% by the time you see it — **"public news already in the price" is
measured true.** The run has barely moved yet still delivers above-baseline forward movement.
Forward-to-already-moved ratio: **run 1.64 vs spike 0.49.**

In *absolute* forward terms the spike still wins (cluster-bootstrapped by name):

- 5× spike: Δmedian forward-5d **+1.178pp**, 95% CI [+0.831, +1.533]
- 3-day run (pure): Δmedian **+0.413pp**, 95% CI [+0.207, +0.732]

Both significant; the spike's edge is **~2.9× larger.** Elevation decays slowly and near-identically for
both (vol ratio at t+10: spike 1.32, run 1.30).

**Verdict for the design: rarity and interestingness come apart here.** Weighting by surprisal alone
ranks the run *below* the spike — the opposite of intent. The run deserves its own dot on the grounds
that it is **unpriced** (1.98% moved vs 8.18%), an axis orthogonal to rarity. **Emit both and carry
"how much has already moved" as a separate field.**

## 7. Intraday vs daily — a dot can fire during the session

Partial-day volume vs the same name's trailing-median volume at the same clock time:

| time | corr(log pace, log full day) | fires on | P(day ends ≥2× \| pace ≥2×) | lift vs 7.1% base | recall |
|---|---|---|---|---|---|
| 10:00 | 0.730 | 13.99% | 37.0% | 5.24× | 73.4% |
| **10:30** | **0.802** | 12.06% | **44.7%** | **6.34×** | **76.4%** |
| 11:00 | 0.843 | 11.09% | 50.2% | 7.11× | 78.9% |
| 12:00 | 0.897 | 9.89% | 59.3% | 8.40× | 83.1% |

**At 10:30 you already hold 76.4% of the eventual ≥2× days at 44.7% precision.** The dot should fire
intraday from ~10:30, labelled as a pace reading with its ~45% close-through rate.

## 8. Verdict

**Yes — worth a dot, but two things must change first.**

- **It currently scores zero.** `background_crowd = {rvol_ex_prints}` is in `ANNOTATE_ONLY_GROUPS` and
  `_attach_rvol` hardcodes `points=0.0`. The 2,700 rvol dots are cosmetic; only 8 days deep.
- **Its baseline caps surprisal at 4.4 bits.**

**Priority order:**

1. **Un-freeze `equity_bars_5min`** — the whole asset, stopped 2026-07-27 with no cron. Cheapest,
   highest-value fix here.
2. Move the rvol baseline from 20-day `cumfrac` to a **120-day per-name empirical rank** off that store
   (floor 4.8% → 0.83%; 4.4 → 6.9 bits).
3. Add the **range/gap sub-dots** — true range is the best-behaved quantity measured and is unused.
4. Emit **persistence and spike as separate dots** with an "already moved" field.

**Fundamentally not measurable:** rarity below 1/(W+1) per name; month-of-year / earnings-season
seasonality (one year, confounded with regime, burn-in deletes Aug/Sep); and **why** volume is
elevated — this channel says "unusual", never "because".

**Caveats:** all numbers are one year in one regime. Forward-return effects, while significant, are
small (+0.41pp median for runs) and are **not** an edge claim. Market caps are point-in-time as of the
last close, so band membership drifts for names that crossed $10B during the year.
