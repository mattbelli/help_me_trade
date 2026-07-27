# Multi-Day Directional Flow Baselines — Pre-Registration (2026-07-26, before first data)

*Written BEFORE the whole-market OPRA `tcbbo` corpus lands. Same discipline as
`CLASSIFICATION_LAB_PREREG_2026-07-16.md` and the earnings_drift precedent: the scoring rules
may not be revised after seeing results. Any revision gets a dated addendum and re-scores from
day 1.*

## The question

Does a **multi-day, per-name directional options-flow baseline with change detection** predict
forward returns over 3–45 trading days?

Founder's framing (2026-07-25), which this test exists to evaluate rather than assume:

> "over days of watching a stock understand its baselines — and then when things change — this
> could be the time to act for a swing trade … capturing days of contested flow percentages and
> understanding when things change is most likely the better method than being like oh hey big
> call sweep and then 5 minutes later oh no bear sweep now we dont know what is what"

The claim has two halves and both are tested: (a) a name's own flow distribution is stable enough
to form a baseline, and (b) deviations from it carry forward-return information at swing horizons.

## Why this is newly answerable

Until 2026-07-26 direction existed only for prints on the ~1,000 OPRA contracts we could afford to
quote — measured at **~59% classified overall and 41.8% unclassified in the opening 30 minutes**,
concentrated in whichever names ranked high on prior-session OI. Every baseline built on that was
selection-biased by our own budget.

The Databento subscription removes the constraint: whole-market `tcbbo` carries the NBBO on every
print, so **aggressor side is known for 100% of trades on 100% of contracts**. Universe is, for the
first time, not a design variable. It must not become one by accident — see "Universe" below.

## Data

- **Primary:** OPRA `tcbbo`, whole market, `2025-07-25 → 2026-07-25` (the included L1 window).
- **Prices/ATR:** daily bars for each underlying; ATR14 as of the day before the event.
- **Excluded:** our own `options_flow_events` store. It holds only *detected* events, so its
  population is shaped by detector thresholds that have changed over time. It cannot back an
  unbiased baseline and is not used here.

⚠️ **Rolling-window deadline.** The free L1 window is exactly 12 months and rolls daily
(measured: 12mo = $0.00, 18mo = $14,964.49). Any day not on local disk before it ages out becomes
a purchase. The corpus must be archived, not re-pulled on demand.

## Print classification (fixed now)

Per print, against the NBBO attached to it:

| side | rule |
|---|---|
| `buy_side` | price ≥ ask |
| `sell_side` | price ≤ bid |
| `mid` | strictly between bid and ask |

**Mid prints are NOT assigned a direction in the primary arm** — they are reported as their own
mass. A pre-registered secondary arm assigns them by tick test. Both are declared now so neither
can be chosen after seeing which works.

Dropped prints: bid or ask null/zero, crossed quotes (ask < bid), or spread > 50% of mid. The
dropped share is recorded per name-day; a name-day dropping > 20% is excluded and counted.

## Daily per-name aggregate

For each `(date, underlying)`, premium (`price × size × 100`) split into
`call_buy, call_sell, put_buy, put_sell` (plus `*_mid`), then:

```
bull = call_buy  + put_sell
bear = call_sell + put_buy
tilt = (bull − bear) / (bull + bear)          ∈ [−1, +1]
```

**Premium-weighted, not print-count-weighted** — a $2M sweep is not a 5-lot.

Two arms, both pre-registered and both reported:
- **ALL** — every contract.
- **SHORT_OTM** — DTE ≤ 30 and |K/S − 1| ≥ 2%, where the academic prior says informational flow
  concentrates. Moneyness is computed at ingest against the underlying's price at print time;
  it cannot be recovered later.

## Baseline and change detection

- Trailing **20 trading days** of the name's own `tilt`, excluding the current day.
- `z = (tilt_today − mean₂₀) / std₂₀`
- Requires ≥ 15 of 20 days present, else no signal for that name-day.
- **Liquidity floor:** ≥ $250,000 total option premium on the day.
- **Event:** `|z| ≥ 2.0`; signal direction = `sign(z)`.
- **Secondary (persistence):** `|z| ≥ 1.5` on two consecutive days with the same sign — the
  founder's "these past 2 days" formulation.

Lookback, threshold and floor are **fixed now**. They are not swept for the best value; see the
split rule below.

## Grading — MFE/MAE, ATR-normalized

From the **next session's open**, over horizons of **3, 10, 21, 45 trading days**:

| metric | definition | what it measures |
|---|---|---|
| **MFE** | max favorable excursion ÷ ATR14 | was the *direction* right — selection quality |
| **MAE** | max adverse excursion ÷ ATR14 | heat taken before it worked — the entry/stop requirement |
| **terminal** | (close_H − open₀) ÷ ATR14 | what a hold-to-horizon actually captured |
| **giveback** | MFE − terminal | exit quality |

All direction-signed (long if z > 0, short if z < 0). **Everything is in ATR units, never percent.**
This is deliberate: `signals/exit_evaluators.py:49` carries a flat `stop_pct = -3.0` for every
symbol regardless of volatility, which is the same class of error MIT §4.3.7 documents — a sound
idea that failed on hard-coded absolute values. Percent returns are not comparable across names.

The decomposition exists because selection alone is not a strategy (founder, 2026-07-25: *"thats
only one aspect of the strategy right? we need to know when to enter and exit"*). MAE sets the stop
requirement empirically; giveback sets the exit rule.

## Baselines it must beat

1. **No-signal** — same names, all eligible non-event days, same horizons.
2. **Buy-and-hold** — the underlying over the same horizon.
3. **SPY** — alpha over the same window.
4. **Naive single-print** — one large sweep (≥ $500k premium, DTE ≤ 30) with no baseline context.
   This is the mechanism we run today; beating it is the minimum bar for the change-detection idea
   to be worth building.

## Statistical bar (MIT §4.3.6)

- **N ≥ 30 events per cell.** Cells below this are reported but never carry a conclusion.
- `t = avg / STD × √N`; require **t > 2.423** (99%, ~31 observations) on the primary metric.
- Cohort must span up, down and sideways regimes; regime composition reported with every result.

## Three-way split (MIT §4.2) — the anti-overfitting rule

| split | window | use |
|---|---|---|
| **train** | 2025-07-25 → 2026-03-31 | any parameter exploration happens here, and only here |
| **validation** | 2026-04-01 → 2026-05-31 | confirms what train chose |
| **test** | 2026-06-01 → 2026-07-25 | touched **exactly once**, at the end |

If the test split fails, the result is **failed**. Not "retune and re-run." A post-hoc variant that
works becomes a new pre-registration with its own test window, never a rescue of this one.

## Registered sub-hypothesis — the mid-cap tier

Founder's stated belief (2026-07-25), registered here so it can be **wrong**:

> "this may work best on our medium sized stocks — our largest stocks and market etfs have so much
> data that it is tough to trade those based on that — but our sweetspot will be a tier below —
> momentum stocks — up and coming companies"

Underlyings are bucketed into **quintiles by trailing-60-day median daily option premium**. Effect
size is reported per quintile. The prediction is that middle quintiles show a larger effect than
the top. If mega-caps win, that is the finding and it gets recorded as such.

This matters because our current coverage cannot answer it: measured determined-direction rates run
NVDA 71.0% / SPX 73.9% down to AMZN 28.9% / GLD 22.0% / CRWD 17.7% — the tier the hypothesis is
about is exactly the tier we see worst. Whole-market `tcbbo` removes that artifact.

## Confounds we do NOT filter

Dealer hedging, index arbitrage, covered-call overwriting and the quarterly collar all add premium
without directional information, and they concentrate in mega-caps and ETFs — the mechanism behind
the mid-cap hunch.

The **primary arm is raw, unfiltered**. One pre-registered secondary arm excludes ETF and index
roots (SPY, QQQ, IWM, SPX, SPXW, XL*, GLD, SLV, TLT). Any filter beyond that is a new
pre-registration, not an adjustment to this one. Inventing hedging filters after seeing results is
exactly how a null becomes a false positive.

## Falsification (stated now)

The mechanism is **dead** if, on the test split, the event cohort's MFE/MAE ratio and terminal alpha
are statistically indistinguishable from the no-signal baseline at **all four horizons across all
five quintiles**. No partial rescue. A subgroup that survives becomes a hypothesis for a fresh
pre-registration.

## Honesty constraints

- **No win% is surfaced anywhere** from this study unless Wilson-bounded and calibrated. Until then
  this is a *perception source* and is labeled as one.
- Backtest **levels** are not claimed; only rankings and relative effects, per the earnings_drift
  lesson where levels were fiction and only orderings survived.
- MIT §6.2.1 haircut prior applied to any forward expectation: live return ≈ ½ backtest,
  drawdown ≈ 2×.

## Operational requirements

- **Schema version stamped on every rollup row.** If dte/moneyness buckets ever change, old rows
  stop being comparable and silently mixing them is the same bug class as retuning a detector
  threshold and invalidating every historical baseline.
- **Direction is never aggregated away.** `options_flow_daily` (183,622 rows) was rolled up from
  option *bars* and has no direction column — which is the entire reason this study needs a new
  corpus. That mistake does not get repeated.
- Rollups keep **dispersion, not just totals** — a z-score needs variance.
- Corpus lives in its own database, never in `trading.db`.
