# IV-Rank Baseline Study — Pre-Registration (2026-07-25, before any outcome data)

Recorded BEFORE computing a single VRP number, per the standing prereg rule
(`CLASSIFICATION_LAB_PREREG_2026-07-16.md`, earnings_drift discipline): the metrics
and thresholds below may not be revised after seeing results. Any revision gets a
dated addendum and re-scores from scratch.

**What this gates:** whether `options-income-lane` (the wheel + covered calls) opens
at all, and if it does, whether an **IV-rank entry gate** is part of it or decoration.

**Question:** premium selling rests on one premise — implied vol exceeds subsequent
realized vol often enough to pay. Two things need separating, because they have
different consequences:
1. **Premise:** is the variance risk premium (IV − forward RV) positive at all on our
   investable universe?
2. **Conditioning:** does **IV rank** — a symbol's IV as a percentile of its own
   trailing year, the practitioner's standard "sell when rank is high" rule — actually
   predict a *larger* VRP? Or is selling at high rank no better than selling always?

## Instruments — measured 2026-07-25, not assumed

Both were calibrated before this document was written. Calibrating an instrument is
not outcome-peeking; no VRP was computed.

- **`hv_current` is exactly the annualized (√252) stdev of the 30 trailing daily
  close-to-close log returns**, ending on the row's own date. Reproduced from our own
  Alpaca bars across SPY/AAPL/XLF/DIA, n=1,544 overlapping dates: **max |err| =
  0.00005**, the rounding of the stored 4-decimal value (one AAPL date at 0.001,
  presumed a dividend adjustment). This is the enabler — **forward realized vol is
  `hv_current` read 30 sessions later**, needing no extra data and introducing no
  second-vendor mismatch.
- **`iv_year_high` / `iv_year_low` are point-in-time.** Zero future-dated extreme
  stamps, they always bracket the row's own `iv_current`, and they reproduce the
  trailing-year max computed from the corpus itself (0/60 mismatch SPY and XLF, 1/60
  AAPL). So vendor IV rank is usable directly and carries no lookahead.
- **`iv_current`'s exact horizon is NOT established.** Our own 30-day constant-maturity
  ATM IV agreed with it to ~3% relative (SPY 0.1566 vs 0.1524; AAPL 0.2959 vs 0.3075,
  across a one-week gap), which is consistent with ~30-day ATM but does not prove it.
  This is the study's main known weakness and is why H4 below exists.

## Data

`data/options_iv_history.db` → `volatility_history`, 1,878,577 rows, 2,311 symbols,
2019-02-09 → 2026-07-24. Externally licensed: research use only, DERIVED values only
if ever surfaced multi-user.

Known, pre-declared limitations — these are reported with every result, not discovered later:
- **The corpus is a curated set, not a market.** 2,311 symbols; QQQ, IWM, GLD, SLV,
  TLT, EEM, ARKK and VIXM are absent even though SPY, DIA and XLF are present. Only
  **37 of the chain sampler's 53-symbol universe** are in it. No cross-sectional result
  may be described as "the market" — it is this curated set.
- **Density is not uniform:** ~60% of sessions through 2020-22, near-daily from 2025.
- **Selection into the set is unknown** and may be survivorship-flavoured. Any absolute
  VRP level is therefore reported as *suspect in level, trusted in ordering* — the same
  demotion the earnings_drift backtest carries (levels fiction, rankings valid).

## Definitions (fixed now)

For symbol *s* on date *t*:
- `IV(t)` = `iv_current`.
- `RV_fwd(t)` = `hv_current` at the row **30 trading sessions after t** for the same
  symbol, i.e. realized vol over (t, t+30]. Requires that row to exist in the corpus;
  if the corpus skips it, the observation is **dropped and counted**, never
  interpolated to the nearest date.
- `VRP(t) = IV(t) − RV_fwd(t)`, in annualized vol points. **Sign convention: positive
  means the option was sold rich.**
- `IVR_range(t) = (IV − iv_year_low) / (iv_year_high − iv_year_low)`, vendor columns —
  the practitioner's "IV rank". Undefined (dropped) when high == low.
- `IVR_pct(t)` = fraction of that symbol's own `iv_current` observations in
  (t−365d, t] that are ≤ IV(t) — "IV percentile", computed by us. **Requires ≥ 120
  observations in the trailing year**, else dropped: a percentile over a thin window
  is noise wearing a number's clothes.

Both rank flavours are carried through every result. They are not interchangeable and
disagreement between them is itself a finding.

## Hypotheses and the exact tests

- **H1 (premise).** Mean VRP > 0 on the pooled panel.
- **H2 (conditioning).** Mean VRP in the top IV-rank decile > mean VRP in the bottom
  decile.
- **H3 (monotonicity).** VRP rises across rank deciles — Spearman correlation between
  decile index and its mean VRP.
- **H4 (horizon robustness).** `hv_current` is a 30-**session** (~42 calendar day)
  window while `iv_current` is probably a ~30-**calendar-day** implied. Both are
  annualized so they are comparable to first order, but term structure makes the
  mismatch a real second-order bias. Re-run the primary on forward RV computed from
  our own bars over **21 sessions** (≈30 calendar days) for the symbols where we hold
  bars. **H1/H2 must survive in sign and decile ordering, or the result is reported as
  horizon-dependent and gates nothing.**

## Statistics — the overlap problem is the main threat and is handled up front

Daily observations with a 30-session forward window are ~30× overlapping, and every
symbol crashes at the same time as every other. Naive standard errors on ~1.8M rows
would be off by more than an order of magnitude and would manufacture significance.

- **Primary CI: block bootstrap resampling whole calendar MONTHS** (2,000 draws),
  which absorbs both the within-symbol overlap and the cross-sectional dependence in a
  single unit. Symbol-level and date-level clustered errors are reported alongside as
  a sanity spread, never as the headline.
- **Effective sample size is reported everywhere** — the count of independent
  symbol-months, not the row count. Any result quoting row-count *n* is wrong here.
- **All results reported twice: with and without 2020-02-01 → 2020-04-30.** One vol
  event that large can carry an entire panel result, and a wheel strategy would have
  been assigned into exactly it.

## Tail reporting — mandatory, not optional

Mean VRP is the number that gets people blown up, because short premium is short a fat
left tail: a positive mean coexists with rare losses many times the average premium.
Every reported mean is accompanied by: the **5th percentile of VRP**, the **worst
single symbol-month**, and the **share of observations with VRP < 0**, per rank decile.
A conclusion citing a mean without its tail is a protocol violation.

## Thresholds — set now, before data

- **H1 passes** if pooled mean VRP > 0 with a 95% month-block bootstrap CI **excluding
  zero**, both with and without the 2020 window.
- **H2 passes** if (top decile − bottom decile) mean VRP > 0 with a 95% CI **excluding
  zero**.
- **H3 passes** if Spearman(decile index, mean VRP) **≥ 0.6**.
- **H4 passes** if H1 and H2 keep their sign and the decile ordering survives at the
  21-session horizon.

## What each outcome licenses — decided before seeing results

| Outcome | Consequence for `options-income-lane` |
|---|---|
| H1 fails | The lane does **not** open on a VRP premise. Its rationale would have to be something else entirely, freshly argued. |
| H1 passes, H2/H3 fail | The lane **may** open, but **without** an IV-rank entry gate — rank would be decoration, and shipping it would be a fabricated rationale. Entry timing needs another basis. |
| H1, H2, H3 pass | IV rank is a legitimate entry gate. Licenses **building a backtest** — not arming a lane. |
| H4 fails | Everything above is reported as horizon-dependent and gates nothing until resolved on our own bars. |

## The honesty boundary on this study

**VRP in vol points is not P&L.** It measures whether options were priced rich, not
what the wheel would have earned: strike selection, delta, assignment path, gap risk,
early exercise, commissions and the borrow/margin side are all absent. A positive
result licenses a *backtest of an actual strategy*, never a live lane and never a win%.
No number from this study may reach a user-facing surface as a scored signal, and
nothing here produces a calibrated win% — that remains the calibration pack's job.

## Outputs

- `data/research/iv_rank_baseline/` — per-decile tables, bootstrap draws, the H4 arm.
- One BUILD_LOG row with the verdict against each threshold above, whichever way it goes.
- A **null result is a publishable outcome here** and closes the question just as
  usefully as a positive one; it is recorded with the same prominence.
