# Channel audit 04 — News

*Agent: Opus, read-only, 2026-08-11. Substrate: `data/trading.db` → `news_items` / `news_symbols`.*
*Component audited: `news` (21,319 dots).*

**Independently verified by the orchestrator:** 21,357 items, `min(received_at)` = 2026-07-22,
**13 full trading sessions**. Weekday volume 1,200–2,400/day, weekend ~30–56/day.

---

## PUNCHLINE

**NEWS should ship as a SUPPRESSOR / context flag, not as evidence.** Three measurements force it:

1. **A news burst is coincident, not predictive.** Median |return| on a burst day = **1.283** per-name
   sd vs **0.547** on a zero-news day (2.35×, Mann-Whitney p=1.1e-65). Forward 3-day: **1.01×, p=0.57
   (nothing)**. Forward 5-day: **0.90× — negative.** The dot marks the day the move already happened.
2. **It is 70% an earnings-date detector, and we already have earnings dates for free.** For 92.7% of
   names, the 13-session headline-count *maximum* lands on the earnings day or the next session. The
   median name puts **69.8%** of its whole 13-session headline total into that 2-session window.
3. **The suppressor mechanic is measured and strong, across three independent flow channels.** On a
   flow-unusual day (z≥1.5), same-day |return| is **1.87–1.94 sd** if news burst vs **0.59–0.80 sd** if
   no news — and the quiet case is statistically indistinguishable from an ordinary day in 2 of 3
   channels (p=0.17, 0.51). p(loud>quiet) < 1e-4 in all three.

**And the rarity→surprisal design does not work on this channel yet.** Not because of shape — because
of depth. **13 full trading sessions.** Empirical-tail floor = 1/13 = 0.077 → surprisal ceiling
**2.56 nats (3.70 bits)**, hard. A 13-session λ̂ produces a 90% surprisal spread of **±1.3 nats** on a
quantity whose whole useful range is ~0–5 nats.

---

## 1. Substrate inventory

`news_items` (21,351 rows) + `news_symbols` (35,520 rows, 5,576 distinct symbols). Producer:
`signals/news_stream.py`, WS `wss://stream.data.alpaca.markets/v1beta1/news` subscribed to `*`.

| fact | value |
|---|---|
| sources | **benzinga 21,337 / empty 14** — single source, no multi-vendor syndication |
| authors | 71 distinct; **85.5%** are `Benzinga Newsdesk` / `Benzinga Insights` (the automated wire) |
| collection start | `min(received_at)` = **2026-07-22T20:24 UTC** |
| full trading sessions | **13** (2026-07-23 … 2026-08-10) |
| volume | weekday **~1,200–2,400 items/day**, weekend **~40/day** |
| in-window | 19,407 items → 30,498 symbol-tags → 5,221 symbols |
| tags/item | 86.4% tag exactly 1 symbol; mean 1.67 |
| publish clock (ET) | 17.8% land in the **16:00 hour**; 35.1% before 09:00; only ~40% intraday |

**Stale-timestamp defect — the "43" is DOTS, not items.**

```sql
select count(*) from news_items where published_at<'2024-01-01';                       -- 16
select count(*) from evidence_dots where component='news' and ts<'2024-01-01';          -- 43
select count(*) from news_items where published_at<'2026-07-22';                        -- 482
select count(*) from evidence_dots where component='news' and ts<'2026-07-22';          -- 1,825
select count(*) from evidence_dots where component='news' and ts<'2026-07-22' and points>0; -- 0
```

- **16** items pre-2024 (43 dots after symbol fan-out). All 16 arrived in **one batch** at
  `received_at` 2026-08-01T03:11:11 UTC; 14 of 16 carry `source=''`; all are SPX/SPY-tagged index
  commentary. Confined to a single REST backfill event.
- The larger defect is **482 items (2.26%) published before collection started**, spread over ~23
  separate `received_at` hours — on-demand symbol-scoped REST pulls. These touch **1,425 distinct
  symbols / 2,777 symbol-tags.**
- **Scoring impact today: zero.** `_news_fresh` (intake.py:1173) rejects all of them. But they corrupt
  the dot timeline. **Build baselines from `news_items.published_at`, never from the dot table.**

**Current detector** (`signals/fusion/intake.py` :1157, :1380–1420; `config/idea_engine.py` :128–130):

- `FUSION_NEWS_WINDOW_MIN = 120` **session-minutes**
- `FUSION_NEWS_BURST_POINTS = ((4, 1.0), (2, 0.75), (1, 0.5))`
- `FUSION_NEWS_MAX_SYMBOLS = 12` — more tags ⇒ roundup, mints at 0.0
- 21,329 dots, **1,024 score (4.80%)**, 378 distinct symbols. **359 of 1,024 scoring dots have
  `count_in_window` = 1** — 35% of all scoring news dots are one headline. **It is not a burst
  detector; it's an "any headline on a watched name" detector.**

## 2. Distribution shape

**Zero-inflated: 80.4% of symbol-sessions are zero** across the 5,221 symbols that got any news at all.
Against a realistic ~11k universe it is far sparser.

Nonzero counts: median 1, p75 2, p90 4, p95 7, p99 16, max 60. 8,008 of 13,277 nonzero symbol-sessions
are exactly 1 headline.

| tier (headlines / 13 sessions) | names | % zero-days | mean | p90 | p99 | max | var/mean |
|---|---|---|---|---|---|---|---|
| A ≥39 (≥3/day) | 54 | 9.5% | 8.13 | 20 | 46 | 60 | 11.4 |
| B 13–38 | 419 | 48.9% | 1.50 | 4 | 14 | 25 | 5.1 |
| C 5–12 | 1,367 | 73.7% | 0.56 | 2 | 6 | 12 | 3.0 |
| D 2–4 | 1,871 | 84.9% | 0.21 | 1 | 3 | 4 | 1.5 |
| E 1 | 1,510 | 92.3% | 0.08 | 0 | 1 | 1 | 0.9 |

**The overdispersion IS the earnings event.** Pooled NB dispersion k=0.39 (severe). Remove
earnings-window days and the process is close to Poisson — at nominal α=0.01 observed exceedance is
**1.47% for Poisson** vs 0.44% for NB(0.39). Including earnings days, Poisson over-fires **2.9×**.
So: *news counts are ~Poisson plus a scheduled, calendar-known spike.*

**Day-of-week: mild.** Mon **0.85×**, Tue 1.01×, Wed 1.07×, Thu **1.13×**, Fri 0.97×.

**Earnings-season seasonality: UNMEASURABLE.** All 13 sessions sit inside Q2 reporting. The
*within-window* earnings effect is enormous — mean 6.40 headlines on the (d, d+1) window vs 0.48
off-day, **13.3×** — and 1,713 earnings events land in the window across 1,688 symbols. **Any baseline
learned here is inflated 2.54×** relative to a name's off-season rate.

## 3. Coverage

**Criterion: ≥13 headlines over 13 sessions (≥1/day).** Below that the modal day is zero and "extreme"
degenerates to "n≥1". At 3 headlines/13 days (the *median* name), P(N≥1)≈0.23 and the λ̂ 90% CI is
[0.107, 0.464] — a 4.3× span.

| threshold | names | share of the 5,221 |
|---|---|---|
| ≥1 headline | 5,221 | 100% |
| ≥2 | 3,711 | 71.1% |
| ≥5 | 1,840 | 35.2% |
| **≥13 (≥1/day)** | **473** | **9.1%** |
| ≥39 (≥3/day) | 54 | 1.0% |
| news on all 13 sessions | 45 | 0.9% |

**Measurable: ~473 names (9.1%). Unmeasurable: 4,748 (90.9%)**, of which 1,510 have literally one
headline in the whole window.

## 4. Per-name normalization → rarity

1. **Two-part (hurdle) model, not a single count model.** Zero-inflation isn't nuisance — it's the
   majority. Estimate π_s = P(N>0) and, conditional on N>0, a truncated count law.
2. **Shrink λ toward the coverage-tier mean**, don't fit per name. With D=13 the raw MLE is unusable.
3. **Exclude the (d, d+1) earnings window from the baseline** and score earnings days against a
   *separate* earnings-day baseline. Not optional: it is 69.8% of the median name's mass.
4. **Deseason by day-of-week** and by a market-wide daily volume index (per-session mean headlines/name
   ranged 1.77 → 2.64 within the window, a 1.5× swing).

**Where it breaks — three places:**

- **Discreteness.** For 90% of names the only reachable events are N≥1, N≥2. Surprisal takes 2–3
  distinct values. There is no continuum to weight.
- **Estimation noise swamps the signal.** Parametric bootstrap, true λ known, 13 sessions, threshold at
  the true 95th pct: 90% spread of estimated surprisal is **2.5–3.2 nats** around a true value of
  3.4–4.2. At 60 sessions the spread falls to 1.35 nats; at 250 sessions, 0.66. **Surprisal weighting
  is not honest here until ~60 sessions exist, and not good until ~250.**
- **The tail estimator saturates.** True p=0.007 → p̂=0 in **91.4%** of 13-day draws. Rare and very-rare
  are indistinguishable.

## 5. Measurable floor and ceiling

- **Empirical tail: p_min = 1/13 = 0.077 → 2.56 nats / 3.70 bits.** With Laplace, 2.64 nats.
- **Parametric extrapolation buys you to about the 1% level (≈4.6 nats)** and no further.
- **Floor:** the smallest *distinguishable* elevation is N≥1 for 90% of names — surprisal ~1.2–1.5 nats.
  **The entire honestly-measurable dynamic range is roughly 1.4 → 2.6 nats, about one bit.** Not enough
  to be additive with a channel that has years of history.

## 6. Persistence

On the 473 measurable names, marking a session elevated at max(per-name p90, 3):

- Run lengths: **456 runs of 1 (69.2%), 187 of 2 (30.8%), 16 of 3 (2.4%), none longer.**
- P(elevated tomorrow | elevated today) = **0.267** vs base 0.143 (1.87×).
- Lag-1 autocorrelation: tier A 0.141, B 0.127, C 0.079, D −0.015, E −0.083. Weak, decaying to noise
  exactly where coverage thins.
- The 2-day runs are overwhelmingly the earnings d/d+1 pattern.
- Aggregating to a 5-session window collapses the burst structure: median per-name CV falls from
  **1.68 (daily) to 0.69 (5-session rolling).**

**A week of sustained coverage is a distinct object from a one-day press release, and only 2.4% of
episodes reach it — but you cannot estimate its rarity from this store. Revisit at 6 months.**

## 7. Duplication and importance — the syndication prior was WRONG

**Benzinga is a single publisher, not an aggregator.**

- Exact normalized-headline duplicates: **370 surplus items (1.91%)**, almost entirely trading-halt
  notices and daily screener templates.
- Near-duplicates within a symbol-session (Jaccard ≥0.6): **1,250 of 30,498 symbol-item rows = 4.10%.**
  The real near-dups are `CORRECTION:` reissues and pre-market/intraday/after-market variants.

**But count is still a poor importance proxy — for a different reason: composition.**

| class | % of symbol-tags | what it is |
|---|---|---|
| other | 42.3% | genuinely mixed — M&A, FDA, contracts, *and* "If You Invested $100 In X" filler |
| analyst_action | 19.8% | mostly "Maintains Overweight, Lowers PT" — near-zero information |
| earnings_result | 17.9% | the EPS/revenue print |
| screener_roundup | 8.6% | "12 Health Care Stocks Moving In Friday's After-Market Session" |
| price_move | 7.2% | "shares are trading lower" — **a restatement of the price, circular as evidence** |
| guidance / div / offering / halt / 13F | 4.1% | |

Two are actively pathological: `price_move` (7.2%) means the news channel and the price channel are
the same measurement; `screener_roundup` (8.6%) is one wire template fanned across 12 tickers. **On each
name's *peak* news day the mix is 42% filler.** `FUSION_NEWS_MAX_SYMBOLS=12` catches only the >12 case;
the median roundup tags exactly 12 and scores.

**The free structured alternative is better — with one caveat.** `earnings_calendar` in `trading.db`:
**28,722 rows / 3,143 symbols / 2004-11-12 → 2036-12-10**, sourced `edgar` 25,456 + `edgar_estimate`
2,992 + `finnhub` 274. **78.8% of burst symbol-days are on names with EDGAR earnings history.** Caveat:
the `edgar` rows are *confirmatory* — median lead time (report date − `fetched_at`) is **−8.2 days**.
Only the 245 `edgar_estimate` rows in Jul–Aug are forward-looking (median +8.8 days lead). **Overall
only 12.0% of Jul–Aug rows were on disk ≥1 day before the report date.** The real-time 8-K path is fast;
it's the calendar *backfill cadence* that lags. **Fixing that cadence would beat headline counting
outright for the 70% of the signal that is earnings.**

## 8. Verdict — SUPPRESSOR / context flag, not evidence

**(a) It fires when the move is already made.** Same-day |ret| 2.35× normal (p=1.1e-65); fwd-3d 1.01×
(p=0.57); fwd-5d 0.90×. Even splitting out non-earnings bursts (n=188), same-day is 0.899 z vs 0.547
quiet, and fwd-3d is 0.625 — *below* the 0.708 no-news baseline.

**(b) It cleanly separates explained from unexplained unusual activity.** On a flow-unusual day (z≥1.5):

| channel | n loud / quiet | same-day \|ret\| **with** news burst | **without** any news | flow-normal baseline | p(loud>quiet) |
|---|---|---|---|---|---|
| option_sweep | 97 / 151 | **1.939 sd** | 0.679 | 0.609 | <1e-4 |
| dark_pool | 162 / 241 | **1.896 sd** | 0.803 | 0.580 | <1e-4 |
| equity_block | 98 / 164 | **1.867 sd** | 0.590 | 0.603 | <1e-4 |

That is precisely "the options activity is explained, there was an 8-K."

**(c) Largely redundant with a free, forward-looking calendar.** 48.8% of per-name burst days fall in
the earnings (d, d+1) window; 92.7% of names' peak day is that window.

**(d) It cannot honestly carry a surprisal weight today.**

**What to emit instead of a scored dot:**

- `news_explained: true|false` on the name-day, plus the top 2–3 headlines and their class — an
  **annotation the LLM reads when it sees a cluster**, carrying 0 points.
- A **negative-weight or discount** applied to co-firing dots when `news_explained` and same-day |ret|
  ≥ ~1.5 per-name sd.
- Keep the *residual* case as the only candidate for genuine evidence: the 188 non-earnings burst days
  (51.2% of burst days are outside the earnings window; 950 on names with no report in the window at
  all) — the M&A / FDA / contract / regulatory items. **But even this subset shows no forward edge in
  13 sessions.** Annotation, not points.

**★ The half of the founder's thesis that did NOT survive.** "Quiet-but-unusual is where the edge is"
is *not* supported here. Forward-3-day |move| for flow-unusual-and-quiet vs flow-unusual-and-loud:
option_sweep 0.854 vs 0.686 (p=0.56), equity_block 0.770 vs 0.766 (p=0.29), **dark_pool 0.719 vs
0.881 — reversed** (p=0.88). And neither beats the flow-normal baseline (0.719, p=0.62). The first pass
on option_sweep alone looked like confirmation; adding two channels and bootstrap CIs killed it. **The
suppressor case rests entirely on (a)+(b) — news explains the past — and not at all on
quiet-outperforms, which this data has essentially no power to test.**

**Re-measure dates:** ~60 trading sessions makes surprisal estimable (spread 1.35 nats) → **2026-11**;
~250 makes it good (0.66) and gives an off-earnings-season baseline → **2027-08**. The collector is
healthy, single-source, and cheap — nothing else needs to change meanwhile.

**Caveats:** 13 trading sessions, one earnings season, one publisher. Price tests are on the 502-name
`momentum_daily_bars` universe only (large-cap-biased). Cross-channel "unusual" is proxied by raw dot
counts per symbol-day; episode coverage biases that sample toward already-interesting names.
