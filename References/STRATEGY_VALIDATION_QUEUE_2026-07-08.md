# Strategy-Validation Queue — 3 Hypotheses to Confirm or Kill on the PC

Three candidates cleared both adversarial screens (real mechanism · independent · non-redundant · tractable · testable survivorship-free). **That is the whole queue — short and high-conviction by design, not thin.** Each is explicitly a **hypothesis to validate, never an edge**, until it clears the gate below on your PC. All literature magnitudes here are **attributed** ("PEAD lit reports ~X"), never adopted as the app's number.

The diversification payoff is clean: **none of the three is a dip**, and they land in **three distinct independence clusters** — cross-sectional continuation, post-event drift, and calendar/funding-flow — each opening a *new family* relative to your two live edges (`dip_buy` = short-horizon technical mean-reversion; `earnings_drift` = pre-announcement anticipation). This queue diversifies the book and piles onto nothing.

---

## 1 · How to validate these rigorously (the gate every item must pass)

Every candidate below is a protocol to *run*, not a result. Output must map into `research/artifacts/strategy_stats.json` exactly like the two live edges — `win_rate`, `ev_pct`, `ev_ci_pct`, `n_setups`, `per_year_positive`, `validated`, `method`, `note` — and only earns `validated` after clearing **all** of:

- **Survivorship-free data.** Norgate S&P 500 **Current & Past** with point-in-time membership — delisted names present at each date. Non-negotiable for momentum (survivorship inflates winners-on-winners) and the PEAD cross-section. The `turn_of_month` SPY sleeve is the lone exception that doesn't strictly need it (single always-existing ETF).
- **Walk-forward, pre-registered primary cell.** Lock ONE base cell before looking; train/param-select on the early window, evaluate the late window **strictly OOS**. Report IS-vs-OOS Sharpe & EV side by side; **>~40% OOS Sharpe decay = fragile**, flag it.
- **Block-bootstrap CI, net of cost.** Bootstrap the per-trade/per-episode return series with a block length that preserves the strategy's own autocorrelation (per item below), and **cluster overlapping holds by entry-date** — `earnings_drift` collapsed 6,624 trades → 2,044 distinct dates; do the same. Bar to clear: the **net-of-cost 95% EV CI must exclude 0**, exactly as `dip_buy` `[0.38, 1.02]` and `earnings_drift` `[0.43, 1.28]` did.
- **DSR — `research/overfit.py::deflated_sharpe_ratio(returns, n_trials=<#grid cells>, sr_variance=…)`.** Feed `n_trials` = the *actual* number of parameter cells evaluated, not 1. It already penalizes negative skew / fat tails (it reads the series' skew & kurtosis — momentum and PEAD are negatively skewed, so this matters).
- **PBO via CSCV — `research/overfit.py::pbo_cscv(returns_matrix, n_splits=16)`.** Feed the **full grid's per-cell return matrix** (T obs × M cells). With multi-knob grids on thin samples this is the *binding* hurdle: require **PBO < ~0.5**, ideally well under. The effect must hold across most cells, not one lucky corner.
- **Cost / capacity at ~10 bps/side.** Report **gross AND net**; a cell that only survives gross is rejected. Sensitivity at **15 and 20 bps**. Confirm a modest account can fill it — single-name concentration, not market impact, is usually the real limit.

Anything that fails this stays `proposed`/`exploratory` — **never a published win%.**

---

## 2 · Dedup & independence clusters

No two survivors collapse into each other — they are three separate clusters, so dedup is clean. The only work is flagging two *portfolio-level* correlation footnotes so you don't double-count exposure later.

| Cluster | Survivor | Return driver | Relation to the live book |
|---|---|---|---|
| **A — Cross-sectional continuation** | `residual_momentum` | 12-month idiosyncratic (factor-neutralized) under-reaction | Orthogonal to both; opposite sign to `dip_buy` short-term |
| **B — Post-event underreaction drift** | `pead_confirmed` | Multi-week drift after a *confirmed* earnings surprise | Driver-independent of both, but **shares the earnings calendar with `earnings_drift`** |
| **C — Calendar / funding-flow** | `turn_of_month` | Month-boundary institutional flow (payroll/pension/rebalance/dealer funding) | Pure calendar trigger — no price or firm event; fully independent |

**Two footnotes for portfolio construction** (not reasons to re-order the queue):

1. **`residual_momentum` is a *solo* cluster here.** Its own spec warns it correlates ~0.5–0.7 with "candidates #2/#3" — but those were *other momentum ideas from its original screening batch that did NOT survive into this set*. Within this queue it stands alone. If you ever revive those siblings, treat the group as **one momentum sleeve**, not three bets.
2. **`pead_confirmed` vs the existing `earnings_drift` = same calendar, different driver.** `earnings_drift` is *pre*-announcement and exits the day before the print; `pead_confirmed` *enters after* the print, conditioned on the reaction. Low return-correlation, but both concentrate in earnings season — if both ever run live, **budget their combined earnings-week exposure as one capacity envelope.**

Net: continuation + event-drift + calendar-flow. **Zero dips. Zero pile-on.**

---

## 3 · Ranked queue

Ranked by **value-of-validation = independence × mechanism strength × tractability** (NOT by any win%). `residual_momentum` and `turn_of_month` tie at the top of that product; the tie-break is *ceiling vs. cost-to-test* (see §4).

| # | Name | Track | Mechanism (1 line) | Precise entry / exit | Cluster | Why validate first | Data needed |
|---|---|---|---|---|---|---|---|
| **1** | `residual_momentum` | foundry | Under-reaction to slow-diffusing firm-specific news (limited attention + disposition); residual build strips the factor bet that makes vanilla momentum crash | **Monthly.** Rank top-decile on residual info-ratio = 11-mo cumulative FF3-residual return (skip most recent month) ÷ residual σ; buy next open, equal-weight; `SPY<SMA200` → no new entries. **Exit** = drop out of top decile at monthly re-rank; no per-name stop/target | **A** (solo) | Strongest mechanism × highest independence; a genuinely NEW orthogonal sleeve (crash-reduced continuation) — highest ceiling in the queue | Norgate S&P C&P **monthly** TR bars (PIT membership, ≥36m warm-up) **+ Ken French factor monthlies** (Mkt-RF/SMB/HML[/UMD], RF) — external, version-pinned, month-end-aligned. The queue's **one non-price input** |
| **2** | `turn_of_month` | foundry | Month-boundary institutional flow (payroll/pension/index rebalance + dealer month-end funding, "Dash-for-Cash") = small recurring, price-insensitive demand shift | **Calendar-only.** Buy SPY at **close of T-1** (2nd-to-last session of month); **exit close of T+3** (3rd session of new month); flat otherwise; no stop/target in base. ~12 non-overlapping episodes/yr | **C** (solo) | **Cheapest, fastest clean test** — SPY + trading calendar, no survivorship-free data, no factor merge, no event machinery; highest tractability, best info-per-PC-hour | Daily **SPY** bars + exchange trading calendar + VIX (regime filter). **Primary variant needs NO survivorship-free data.** (Cross-section variant needs Norgate C&P — deprioritized) |
| **3** | `pead_confirmed` | foundry | Prices under-react to earnings surprises and drift for weeks; require surprise + price reaction + volume to **agree** to filter already-priced names | **Event-time.** At close of j+1 require market-adj 2-day reaction AR>0, top-tercile, ≥+3%, **and** RVOL[j+1]≥1.5; buy open of j+2, small equal-weight. **Exit T+45 td** (sweep 30–63), −8% stop, optional +5%→10% trail | **B** (solo; calendar-overlaps existing `earnings_drift`) | Meatiest edge IF it survives (multi-% over weeks; cost amortizes well), most-replicated mechanism — **but decay-dominated + data-forced proxy**, so lowest-confidence of the three | Norgate S&P C&P **daily** OHLCV (PIT) + existing earnings-date table (`research/earnings.py`) + SPY. **HARD GAP:** no PIT SUE/consensus survivorship-free → validated leg uses the **EAR proxy**; SUE overlay stays **live-forward-only** via `signals/finnhub.py::afetch_eps_surprises` (`surprisePercent`) and **never** enters the published stat |

---

## 4 · Validate these FIRST (all three — it's the whole queue)

**#1 `residual_momentum` — highest ceiling.** Best mechanism × independence in the set: a 12-month continuation signal with the systematic component regressed out is near-orthogonal to everything you run, and its selling point — halved crash risk / ~doubled Sharpe vs. vanilla momentum (*attributed to Blitz-Huij-Martens 2011 & Huij-Lansdorp 2017 OOS; verify on Norgate*) — is exactly the arbitrage-limiting feature that tends to *persist*. If it clears the gate it's not a marginal overlay, it's a new cross-sectional sleeve. Watch: only ~270 monthly obs (autocorrelated) → block-bootstrap at 6–12-mo blocks, and DSR/PBO are load-bearing given the grid; enforce **excess-over-benchmark** scoring (top-decile carries market beta — never score raw return). The one setup cost is the version-pinned Ken French factor merge.

**#2 `turn_of_month` — cheapest clean yes/no.** If you want a fast first result or to warm up the validation rig, run this first: the primary SPY sleeve needs no survivorship-free data, no factor library, no event alignment — just SPY bars and a trading calendar, ~190 episodes, two knobs that actually matter. Mechanism is causal (dealer month-end funding) and independence is total (calendar trigger). The honest risk is *size*, not cost: heavily published → likely compressed, so it may return "real but too thin net of 10 bps / after PBO." That's still a high-value answer — a cheap decisive kill, or a small orthogonal diversifier. **Benchmark correctly: the TOM window vs. the average non-TOM 5-day window** (plus a random-window placebo), never vs. cash.

**#3 `pead_confirmed` — biggest payoff, lowest confidence.** The mechanism is the most-replicated of the three and the tradeable long-leg drift is *meaty* (lit ~+2–2.5% over ~3 mo — *attributed, an optimistic long-short-halved upper bound; verify*), so 10 bps barely dents it. Two things put it third: **(a)** 2024 literature says classic PEAD has **decayed toward ~0** — the whole bet rides on the **2017-2026 OOS half still paying net**, so make that split a hard gate; **(b)** the survivorship-free signal is forced to the **EAR proxy** (no PIT SUE in-repo — confirmed: `research/earnings.py` stores dates + reaction math only), and EAR (buying strength right after a big up-gap) flirts with generic price momentum, so **run a momentum-neutralized control** to prove it's earnings-specific. More upside than TOM if it survives, but more machinery and more ways to die — validate after the two cleaner tests.

**Sequencing.** Ranked order is `residual_momentum` → `turn_of_month` → `pead_confirmed` by value-of-validation. If PC-time is tight and you want momentum-of-progress, flip the top two and run **`turn_of_month` first** (lowest setup cost, fastest decisive result) — that is the one genuine ordering tradeoff in the queue, and it's yours to make. Everything else is fixed: `pead_confirmed` stays third regardless.

*Every row above is a hypothesis to confirm or kill — not an edge. The screens already cleared mechanism/independence/tractability; the PC's job is magnitude, significance, and survival, not re-litigating the story.*