# RVOL / Intraday Volume Profile — Research

*Saved 2026-06-23. Source: Sonnet research agent (web, ~14 cited sources), salvaged from the
deep-research run. Feeds the **watchlist "volume % above expected"** feature. Companion:
`SCHWAB_MARKETDATA_RESEARCH.md` (the real-volume data source this depends on).*

**Goal:** flag in real time, intraday, when a symbol's volume *so far today* is unusually high
vs. normal *for this time of day* — not waiting for end-of-day, and never comparing to a flat
daily average (intraday volume is U-shaped: heavy open/close, light midday).

## The standard algorithm (academic + practitioner consensus)
Boyd/Busseti (Stanford VWAP execution), Białkowski et al., the ML baselines, and practitioner
RVOL-TOD tools all converge on the same recipe:

1. **Bin the session into fixed time-of-day buckets.** Sizes in the wild: 1-min (Boyd, T=390
   bins/day), 5/15-min (practitioner RVOL-TOD; arXiv 2505.08180 uses 15-min → 26 bins), 20-min
   (Białkowski, 25 buckets). Smaller buckets = noisier, need more smoothing/robustness.
2. **Normalize each bucket to a fraction of that day's total volume** *before* averaging.
3. **Average that fraction per bucket across a rolling window of recent days.**
4. **Renormalize** so the per-bucket vector sums to 1 → the expected volume profile.

Best-practice refinement (Boyd, Białkowski): estimate the *shape* pooled across symbols, then
**scale per symbol** by a per-symbol daily-volume estimate. Volume = per-stock deterministic
daily size × shared stochastic intraday shape.

## Concrete formula
**Busseti & Boyd, "VWAP Optimal Execution"** (https://web.stanford.edu/~boyd/papers/pdf/vwap_opt_exec.pdf) —
plain double average over a rolling window of days (and across stocks):

```
E[m_t / V] ≈ ( Σ_{j=i−W}^{i−1} Σ_{k=1}^{K}  m_t^(j,k) / V^(j,k) ) / (W·K),   for each bucket t = 1…T
```

For each day `j`/stock `k`, divide that minute's volume `m_t` by that day's *total* volume `V`
(normalization), then average over the window. Walk-forward (use only days `i−W…i−1`,
no look-ahead). **W = 20 trading days (~1 month).** Białkowski gives the same classical baseline
`x̂_{i,t} = (1/L) Σ x_{i,t}^{j−l}`, L=20.

## Mean vs median vs EWMA
- **Arithmetic mean over last ~20 days is the dominant baseline** (Boyd W=20, Białkowski L=20,
  RVOL-TOD default 50-period SMA). EWMA is a selectable secondary (faster regime adaptation).
- **Median is NOT the headline for the intraday *shape*** in the literature — robustness to
  spikes is instead handled by **log/log-normal transforms + heavy-tailed (Student-T)
  likelihoods** (Boyd; IVE arXiv 2411.10956; ML papers' log+LASSO). Median *does* appear, but
  for the **daily-volume level** (median dollar volume ~21d for liquidity/ADTV screening), not
  the curve. → For us, **median or trimmed-mean is the cheap practical shortcut** to what the
  heavy-tail models achieve more elaborately.
- **Lookback:** ~20 trading days (≈1 month) for the shape; rationale = recency/non-stationarity
  ("most recent data most relevant... systems change over time" — Boyd). Białkowski: seasonal
  component static only over ≤3 months.

## Normalization
- **Universal:** divide each bucket by the day's total volume → fractions summing to 1
  (Boyd `m_t/V`; arXiv 2502.13722 makes it explicit `Q_t=V_t/V`, `Σ Q_t=1`).
- **De-trend:** divide raw volume by a trailing two-week moving average before fitting the shape
  (with shifting to avoid look-ahead) — arXiv 2502.13722.
- **Cross-symbol:** use **turnover** (volume / shares outstanding) so different floats are
  comparable (Białkowski, following Lo & Wang 2000).
- Cumulative curve is **S-shaped**; its derivative is the classic **U-shape** (heavy 9:30–10:00,
  light 12:00–14:00, heavy 15:00–16:00; final bucket spikes from the closing auction).

## Smoothing & special days
- **Smoothing:** the W-day averaging *is* the smoother; spline/kernel not prominent. Better
  denoiser = **PCA first principal component across symbols** as the smooth seasonal U-shape
  (Białkowski).
- **Outliers:** log/log-normal transform; Student-T heads; LASSO.
- **Special days:** **half-days explicitly excluded** from calibration (Białkowski dropped
  Dec 24 & 31). Earnings/dividends/rebalancing absorbed by the **stock-specific dynamic
  component** (ARMA(1,1)/SETAR residual, CMEM non-periodic term) to keep the seasonal profile
  clean.

## State-of-the-art decompositions (beyond flat average)
- **Białkowski/Darolles/Le Fol (JBF 2008):** volume = common/seasonal (U-shape via PCA, shared)
  + stock-specific dynamic (per-symbol ARMA(1,1)/SETAR). Static vs dynamic schedule.
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=932699
- **CMEM (arXiv 2505.08180):** volume = daily η_t × intraday periodic s_j (U) × non-periodic
  μ_tj. 15-min bins, normalize by day mean + log.
- **Deep learning (arXiv 2502.13722):** optimize the allocation curve directly
  (`q_t = e^{v_t}/Σ e^{v_s}`) rather than blindly following the predicted curve.

## Practitioner version (RVOL-TOD)
Relative Volume Time-of-Day = today's bucket volume / avg of that **same time-of-day bucket**
over prior sessions. 5/15-min buckets; default 50-period SMA (StockCharts), commonly 10–20
sessions in day-trading; SMA or EMA. Key warning: **always same-slot-to-same-slot, never vs a
flat daily average.** https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-volume-rvol

## Recommendation for help_me_trade
- **5-min buckets** (1-min if data permits). For each: volume / day-total.
- Average those fractions over a **rolling ~20–30 trading-day window**; use **median or
  trimmed-mean** for cheap outlier robustness.
- Renormalize so the curve sums to 1. **Exclude half-days and known earnings days.**
- Optional: smooth via first PCA component across symbols, or a light moving average.
- **Metric:** `RVOL(t) = today_cumulative_vol(t) / expected_cumulative_vol(t)` (same-time-of-day).
- **Display (orchestrator rec, not yet confirmed):** multiplier **"2.3×"** + color band:
  `<1.5 normal · 1.5–2 elevated · 2–3 unusual · >3 extreme`. (The display-format research angle
  did not complete; this is a judgment call to confirm.)

## Sources (14)
1. Busseti & Boyd, "VWAP Optimal Execution" — https://web.stanford.edu/~boyd/papers/pdf/vwap_opt_exec.pdf (arXiv 1509.08503)
2. Białkowski, Darolles & Le Fol, "Decomposing Volume for VWAP Strategies" (KIT) — https://www.fbv.kit.edu/symposium/10th/papers/Bialkowski_Darolles_LeFol%20-%20Decomposing%20volume%20for%20VWAP%20strategies.pdf
3. Białkowski et al., "Improving VWAP Strategies: A Dynamic Volume Approach" (JBF 2008) — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=932699
4. "Deep Learning for VWAP Execution in Crypto Markets" — https://arxiv.org/html/2502.13722v2
5. "Forecasting Intraday Volume in Equity Markets with Machine Learning" — https://arxiv.org/html/2505.08180v1
6. "Enhanced Probabilistic Forecasting of Intraday Volume Ratio with Transformers" (IVE) — https://arxiv.org/html/2411.10956v1
7. Wikipedia, "Volume-weighted average price" — https://en.wikipedia.org/wiki/Volume-weighted_average_price
8. Cube Exchange, "What is a VWAP Order?" — https://www.cube.exchange/what-is/vwap-order
9. Endovia Wealth, "Execution Algorithms: VWAP, TWAP, POV" — https://www.endoviawealth.com/execution-algorithms-in-india-vwap-twap-pov-more-explained/
10. StockCharts, "Relative Volume (RVOL)" — https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-volume-rvol
11. TradingSim, "Relative Volume (RVOL)" — https://www.tradingsim.com/blog/relative-volume-rvol
12. ResearchGate, "Optimal VWAP Trading Strategy and Relative Volume" — https://www.researchgate.net/publication/23697062
13. QuantConnect VolumeProfile (caveat: volume-at-price) — https://www.quantconnect.com/docs/v2/writing-algorithms/indicators/supported-indicators/volume-profile
14. TradingView Session Volume Profile (caveat: volume-at-price) — https://www.tradingview.com/support/solutions/43000703072-session-volume-profile/

**Terminology caveat:** "volume profile" is overloaded — chartists mean *volume-at-price*
(POC/value-area); this doc means the *volume-by-time-of-day* U-curve (what we want).

## Display — FINALIZED (2026-06-23, confirmed with founder)
- **Format:** multiplier **"2.3×"** + color band: `<1.5 normal · 1.5–2 elevated · 2–3 unusual · >3 extreme`.
  No explicit "so far / final" marker — number + color is enough.
- **Three states:**
  - **Warming up (first ~15 min after 9:30):** denominator too small → ratio noisy. **Suppress**
    (show "—" / muted) until ~15 min in, then go live.
  - **Live (session open):** `RVOL(t)` updates intraday, clamped so `t` never advances past 16:00.
  - **Settled (after close):** value freezes at the close = full-day RVOL (= today_total /
    expected_daily_total). No special-casing — it's the same formula at `t`=close.
- **Overnight / pre-open:** keep **yesterday's final full-day RVOL, greyed / de-emphasized**; reset at 9:30.
- **RTH-only** volume in the numerator (ignore extended-hours prints; profile is RTH-based).
