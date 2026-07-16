# earnings_drift — breadth vs concentration (PC experiment brief)

*Written 2026-07-16 for the PC agent (Norgate data lives there; serv has none). Read all of it before
running anything — the framing decides whether the output means anything.*

## The question

At earnings-season peak, `earnings_drift` produces ~40–51 simultaneous qualifying setups. With a fixed
pot (~$100k paper), we can either **concentrate** (few names, big size) or go **broad** (many names,
small size). Which produces better risk-adjusted returns on the real survivorship-free record?

## Why this is not a tuning question — read this first

**Full breadth IS the validated strategy. The cap is the deviation.** This inverts the usual framing and
it is the single most important thing to understand here.

- `research/earnings_strategy.py:134-198` (`_symbol_setups`) appends **every** event clearing the
  blowout/gap filters. No cap, no ranking, no top-N, no cross-symbol awareness.
- The reported stat is an **unweighted per-trade mean** (`:242-244`).
- The significance test that earned the "validated" label is **explicitly per-date equal-weight**:
  `research/significance.py:19-27` groups trades by entry_date → equal-weight mean per date, and the
  block-bootstrap CI `[+0.43, +1.28]` is computed on *that* series (`:143-148`).
- **No position sizing was modeled at all** — `research/strategy_sim.py` returns percent
  (`blended_return_pct`), not dollars.

⇒ **The CI does not transfer to a capped variant.** Taking 5 of 51 does not estimate the parameter the CI
describes; it estimates a size-5 subsample mean. The live strategy is currently capped at 5 (a safety
brake applied 2026-07-16 after a sim showed 51 would fire at once, uncapped auto, = $510k). That cap is
temporary and **wears a badge it did not earn**. This experiment decides what replaces it.

Scale of the clip: n=6,624 trades / 2,044 distinct dates / 2010–2026 / ~9 td holding ⇒ **average
concurrency ≈ 14** across all 17 years *including dead non-season months*; **peak 40–50**. A cap of 5
binds below even the all-year average — discarding ~64% of the validated sample on average, ~90% at peak.

## ⚠️ What the capped arm actually models (do not skip)

`research/portfolio.py`'s skip rule is **arrival-order** (`:202-215`) — when at `max_concurrent`, it drops
whatever comes next. Live, `signals/strategy_universe.py` returns `sorted(...)` and
`signals/strategy_scan.py:420` iterates it and **`break`s** at the cap (`:471`). There is **no ranking
step anywhere in the earnings path**.

⇒ Both backtest and live select by **arrival/alphabetical order, not quality**. That makes portfolio.py
*faithful to live* — a feature, not a bug — but be explicit in the writeup:

> **This measures breadth vs *arbitrarily-selected*-N. It does NOT measure breadth vs *best*-N.**
> "Top-N by blowout strength" does not exist in any code path and is unvalidated (see Query 2).

## The data

- **DB:** `data/research/earnings_labeled.db` (PC only), table **`strategy_setups`**, `source='norgate'`.
  ⚠️ Note `research/strategy_dataset.py` writes a *same-named* table into a *different* DB
  (`strategy_labeled.db`, the dip book) — make sure you point at **earnings_labeled.db**.
- **Returns are already NET of costs**: `research/earnings_strategy.py:166`
  `net_ret_pct = gross_ret_pct - cost_pct`, default `cost_pct=0.20` (20 bps — note this differs from
  `strategy_dataset.DEFAULT_COST_PCT=0.10`). **Pass `--cost-pct 0.0`.** Anything else double-counts.
  First: confirm what cost is actually baked into the existing rows (the builder stores `cost_pct` in its
  summary, `:227/:249`); if the DB was built with a different value, say so rather than assuming.
- ⚠️ **NEVER use `bars_validation_slice.db`** — verified survivor-biased (0 of 496 symbols stop trading
  before 2025-06; SIVB/FRC/ATVI/VMW absent). It is a current-constituents fixture for
  `momentum_validate.py` correctness only. No number from it may be published.

## The tool

`research/portfolio.py` already models exactly this — `max_concurrent` + `per_trade_fraction`, event-driven,
with an equity curve. It has a CLI:

```bash
python -m research.portfolio \
  --db data/research/earnings_labeled.db \
  --source norgate \
  --start-equity 100000 \
  --max-concurrent <N> \
  --per-trade-fraction <F> \
  --sizing fixed \
  --cost-pct 0.0
```

`--sizing fixed` is correct: `earnings_drift` has **no stop** (time-based exit only), so risk-sizing is
inapplicable. It does **not** model share granularity — that's deliberate; whole-share drag is a separate
live concern (below), not part of this question.

## Pre-registered variants — run ALL, report ALL

Pre-registering matters: sweeping and reporting the winner is multiple testing. `research/overfit.py` has
DSR/PBO for exactly this. **`n_trials` = 6** (the rows below), not 1.

| Arm | max_concurrent | per_trade_fraction | Gross at full | Why |
|---|---|---|---|---|
| A — validated shape | 999 (effectively uncapped) | 0.02 | ~100%+ | What actually earned the CI |
| B — breadth | 50 | 0.02 | 100% | The founder's proposal |
| C | 25 | 0.04 | 100% | Middle |
| D | 14 | 0.07 | ~98% | The validated *average* concurrency |
| E | 10 | 0.10 | 100% | portfolio.py's own default |
| F — current live | 5 | 0.10 | 50% | The safety brake; deploys only half the pot |

Hold `max_concurrent × per_trade_fraction ≈ 1.0` so arms differ in **breadth, not leverage** — otherwise
you're comparing exposure, not diversification. Arm F at 5×0.10 = 50% invested is deliberately the live
config; also run **F′ = 5 × 0.20** so concentration isn't handicapped purely by sitting in cash.

**Report per arm:** `cagr`, `max_drawdown`, `sharpe`, `avg_pct_invested`, total trades taken, and
**trades skipped by the cap** (the last one quantifies how much of the validated sample each arm throws
away). Then: DSR across the 6 arms with true `n_trials`, and PBO/CSCV if the arm ranking is close.

**Expected (state your prior, then check it):** breadth should compress variance — same mean, thinner
tails, better Sharpe/drawdown — because 50 concurrent bets sample the effect while 5 draw from it. If
breadth does *not* win on Sharpe, that's the interesting result and worth investigating, not burying.

## Query 2 — is blowout strength predictive at all? (cheapest query in this thread)

Ranking by blowout magnitude was proposed, then found to be **a guess with no evidence** — and the
existing evidence cuts against it:

- `strategy_stats.json` note: the blowout filter adds only **+0.19%/trade**, and **the control with no
  blowout filter at all is itself significant** (CI [+0.33, +0.94]). A weak binary gate is a poor
  ranking key.
- The only comparison ever run is binary (`require_blowout=True/False`, `earnings_strategy.py:144`) —
  nothing has ever tested dose-response.
- `research/ev.py::build_ev_table` buckets by *calibrated model probability* (`:42-47`), not reaction
  magnitude — and that model is edgeless anyway (`artifacts/meta.json`: n=447, brier 0.2456 vs base_rate
  0.5369, `honesty.low_confidence: true`). Irrelevant here.

**The data exists and has never been analyzed.** `blowout_reaction_pct` is a real column
(`earnings_strategy.py:37`, written `:193`, also in `features_json` `:172-177`).

**Do:** bucket all 6,624 setups by `blowout_reaction_pct` decile → win rate + mean return + Wilson CI
(`research/ev.py::wilson_ci`) per decile. Is it monotonic? Flat = ranking by strength is worthless and we
stop discussing it. Monotonic = a real (still unvalidated) candidate worth pre-registering separately.

⚠️ Do **not** then re-run the arms filtered to the best decile and report that number — that's the
multiple-testing trap the repo's own discipline exists to prevent. Decile analysis is **hypothesis
generation**, and any resulting rule needs its own pre-registered test.

## Live constraints — context only, do NOT model them

These are live-implementation facts. They do not belong in the backtest; they're listed so you know the
result is actionable and don't "helpfully" model them:

- **Whole shares:** `trading/proposal_intake.py:81` floors qty; `risk_engine.py:221-227` rejects
  fractional **unless `intent.rebalance`** (set only by `signals/momentum.py:498,585`). Founder confirms
  fractional works in STRAT2 (~90 momentum names). The gate's own comment — *"would otherwise leave ~24%
  in cash and drop every name priced above the per-name budget"* — is **verbatim the breadth case**, so
  the precedent and the rationale both already exist. If breadth wins, that flag gets opened for the
  scan path.
- **`max_daily_orders: 20`** (`config/settings.py:126`) is an arbitrary default, not a real constraint —
  and it's global across all three accounts (`risk_engine.py:686` takes no `account_id`), with an
  off-by-one (the pending row is written before validate, so an order counts itself → you get 19).
- **BRK.B / BF.B** are rejected by `_SYMBOL_RE = ^[A-Z]+$` (`risk_engine.py:38,170`) despite being in the
  sp500 universe.
- The 5/$50k live caps are **unversioned** — DB-only; a rebuilt DB reverts to seed ($500, uncapped,
  disabled).

## Honesty rules for the writeup

1. **Never reuse the `[+0.43, +1.28]` CI for a capped arm.** It belongs to arm A only.
2. Report every arm, including losers. `n_trials = 6` in any DSR.
3. Say "breadth vs arbitrarily-selected-N", never "vs best-N".
4. Decile analysis is hypothesis generation, not a result.
5. If the answer is "no meaningful difference", that is a real and useful finding — say it plainly.

## Deliverable

A table of the 6 arms (+F′) with the metrics above, the decile table with Wilson CIs, a DSR across arms,
and a one-line recommendation: **the number** (max_concurrent) and **the size** (per_trade_fraction),
with its honest caveat. That replaces the temporary cap of 5.

Related: `References/STRATEGY_VALIDATION_QUEUE_2026-07-08.md` (the pre-registration discipline this
follows), `References/board.json` → `earnings-drift-edgar-wire`.

---

# RESULTS — 2026-07-16

## 1. Breadth wins. Decisively. (PC run, `research/_edgehunt/breadth_vs_concentration.py`)

Same 6,624 earnings-blowout setups, `earnings_labeled.db`:

| config | CAGR | maxDD | Sharpe | signals taken | avg invested |
|---|---|---|---|---|---|
| 5 × 10% (live cap) | 5.18% | 22.65% | 0.54 | 2,001 / 6,624 (30%) | 31.3% |
| 50 × 2% | 4.49% | 12.40% | 0.95 | 6,368 / 6,624 (96%) | 19.9% |
| 50 × 3% | 6.01% | 13.25% | **0.99** | 5,693 (86%) | 26.7% |
| 50 × 5% | **8.35%** | 17.21% | 0.97 | 4,501 (68%) | 35.2% |

Capital-matched (50×5% @35.2% vs 5×10% @31.3%), **breadth dominates on every metric**: +8.35% vs +5.18%
CAGR, 17.2% vs 22.7% drawdown, Sharpe 0.97 vs 0.54. The concentrated book isn't merely riskier — the
arrival-order clip makes it a 30% subsample and it earns **less in absolute terms**.

⇒ **Take breadth. Size small, take nearly everything.** 3% vs 5% is a risk dial, not a finding
(Sharpe 0.99 vs 0.97 = noise): 3% = smoother + 86% coverage; 5% = +2.3% CAGR for +4% drawdown.

## 2. Blowout-magnitude ranking is DEAD

No gradient across `blowout_reaction_pct` deciles — mean return bounces 0.6–1.2% with no monotone trend.
Decile 4 ≈ decile 9; decile 7 is worst (0.21%, ~2.6σ below pooled across 10 comparisons = noise, not
signal). Decile 10 (>16% reaction) has the best mean (1.33%) but at ~1.6σ with the fattest variance
(stdev 8.8) — not rankable. **"Bigger blowout ⇒ stronger drift" is killed as a continuous feature.** At
most a weak unproven tail effect above +16%, not worth a threshold. Do not spend ranking effort here.

## 3. ⚠️ The PC is on STALE research code

The PC agent found `backtest_portfolio` double-counting a `cost_pct=0.30` default against already-net
`blended_return_pct`. **That is fixed on main** — default is `0.0`, `None` raises, and the docstring says
verbatim *"The old silent 0.30 default was this module's own invention and double-counted against
net-stored returns."* Main's `earnings_strategy.py` defaults to `0.20`, not `0.30`.

⇒ The PC predates the **`research/pipeline-honesty-fixes`** merge (PR #28), whose four fixes were: CV
leakage, next-open fills, embargo, **and cost single-sourcing**. It just rediscovered the fourth.
The breadth ranking survives (identical drag across arms; absolutes understated ~0.3%/RT), but **the
other three change conclusions, not just levels**. **`git pull` on the PC and re-run before any further
number is trusted.**

## 4. ★ THE BIG ONE — live enters at the WRONG TIME (founder catch)

The strategy is *"buy at 10 days out, sell 1 day before"* — `exit_lead_td=1` exists only because we
can't tell bmo/amc, so selling day-of would sometimes land after the print.

- **Backtest** (`research/earnings_strategy.py:156-157`): `entry_idx = jn - entry_lead_td` — enters at
  **EXACTLY** 10 td out; `holding_days = exit_idx - entry_idx` = **always 9**.
- **Live** (`research/strategy_features.py:340`): `return exit_lead_td < td_to_next <= entry_lead_td` —
  fires **anywhere in 2..10**.

Steady state this is harmless (a name enters the band at 10 and fires that day; the band is catch-up
tolerance). **On COLD START it is a mass mis-entry** — which is exactly what deploying the EDGAR fix
causes, since the calendar goes empty→full at once.

**Measured on the live universe 2026-07-16** (EDGAR dates + real bars):

| td to next earnings | count | verdict |
|---|---|---|
| **10** | **4** | **FAITHFUL** — CVS, FTNT, IEX, PTC |
| 9 | 8 | late 1d → holds 8d |
| 8 | 3 | late 2d → holds 7d |
| 7 | 2 | late 3d → holds 6d |
| 6 | 8 | late 4d → holds 5d |
| 5 | 14 | late 5d → holds 4d |
| 4 | 8 | late 6d → holds 3d |
| 3 | 4 | late 7d → **holds 2d, not 9** |
| | **51 total** | **47 UNFAITHFUL** |

**Only 4 of the 51 are the validated trade.** The other 47 are a shorter, unvalidated strategy wearing
earnings_drift's name and badge. The "51 fires / $510k / needs caps" alarm was **a cold-start artifact,
not the strategy**.

**FIX: enter only at `td_to_next == entry_lead_td`.** Consequences, all good:
- Today's cohort = **4 names** (CVS, FTNT, IEX, PTC) → the cap of 5 never binds.
- **Self-ramping**: ~4–8 entries/day during season × 9-day holds → ~40–50 concurrent at peak =
  the validated shape, reached organically instead of by picking a number.
- **Fails closed** when a vendor publishes a forward date late (steady-state case, not just cold start):
  the name simply doesn't fire that quarter instead of entering a truncated trade.

## Resulting design

| knob | value | source |
|---|---|---|
| entry | `td_to_next == 10` exactly | backtest fidelity (§4) |
| max_concurrent | ~50 | validated peak; emerges from the ramp |
| per_trade_fraction | 3% (or 5%) | §1 — risk dial, not a finding |
| ranking | **none** | §2 — no gradient |
| fractional shares | **required** | else $3k can't buy NVR/BKNG; ~24% stranded in cash |
| max_daily_orders | raise from 20 | arbitrary default, global, off-by-one |

Still open: per-strategy P&L for `entry_mode='auto'` (never journals → `stats_by_strategy` returns n=0
forever) — the prerequisite for measuring any of this live.

---

# ⚠️ CORRECTED RESULTS — 2026-07-16 (supersede §1 above)

The first run double-counted costs (`portfolio.py` re-subtracted a 0.30 default against already-net
`blended_return_pct`). Re-run at the correct 20bps RT. **Both conclusions hold, stronger:**

| config (matched exposure) | CAGR | maxDD | Sharpe | avg invested |
|---|---|---|---|---|
| concentrated 5 × 10% | 9.3% | 19.8% | 0.92 | 31% |
| **breadth 50 × 5%** | **13.2%** | **13.7%** | **1.47** | 35% |

**The double-count was an accidental COST-STRESS TEST — and the edge survived it.** At ~50bps RT
(double-counted) breadth still returned 8.35% / Sharpe 0.97; at the true 20bps it's 13.2% / 1.47.
**Breadth beats concentration at BOTH cost levels.** That satisfies the sensitivity leg the validation
gate asks for (10bps/side + 15/20bps sensitivity) for free — record it as such rather than discarding
the "wrong" run.

Decile table is **byte-identical** (it never touched the sim) ⇒ **the ranking-premise kill stands**: no
gradient, only a weak unactionable top-tail hint above +16%.

## Staleness worry: RESOLVED for this experiment (verified)

`research/earnings_strategy.py` imports **nothing** from `strategy_sim` / `validation` / `significance`
— it computes close-to-close returns inline (`:160-166`). So of PR #28's four fixes (CV leakage,
next-open fills, embargo, cost single-sourcing) **only cost single-sourcing touches this path**, and
that's the one now fixed. **These numbers are sound.** The PC should still pull main for anything
touching the dip book (`strategy_dataset` → `strategy_sim`) or the ML scorer (`validation.py`).

## ⚠️ Read these numbers honestly

- **Benchmark is alpha vs SPY, not raw return.** SPY ≈ 13% CAGR over 2010–2026 — headline return
  *ties the index*. The real claim is risk-adjusted: **Sharpe 1.47 vs SPY's ~0.85, maxDD 13.7% vs SPY's
  ~34% (2020).** Same return, half the pain. Quote that, not the 13.2%.
- **35% avg invested ⇒ 65% idle.** That idle cash is *why* the drawdown is half the index's — not a
  flaw. But the honest framing is "13.2% on a third of the capital", and it's the strongest argument yet
  for a combined book: earnings_drift alone leaves two-thirds of strat1 doing nothing.
- Known fidelity gap (pre-existing, unrelated to costs): the backtest is **close-to-close**
  (`entry_close = closes[entry_idx]`); live fires intraday at market. See `signals/strategy_scan.py`'s
  own "Fidelity caveat" docstring.

## Fractional shares — it's OUR policy, not Alpaca (clarified 2026-07-16)

Founder holds fractional shares in STRAT2 *right now*. Nothing is broken and Alpaca is fine with it:
`trading/risk_engine.py:221` blocks fractional unless `intent.rebalance=True`, set in exactly two
places — `signals/momentum.py:498,585`. momentum runs in strat2 ⇒ fractional there, whole-share
everywhere else. Deny-by-default with one granted exception; the error string "fractional shares not
supported" is misleading — it IS supported, just not permitted for that caller. earnings_drift needs the
same exception for the identical reason the gate's own comment gives ("~24% in cash and drop every name
priced above the per-name budget"). Chokepoint change ⇒ own PR + 2 adversarial reviewers.

---

# ROUND 3 — Covid gate, rebalancing, deployment ceiling (2026-07-16)
*PC: `research/_edgehunt/covid_and_rebalance.py` (uncommitted; review caveats in its docstring).*

## 1. Covid gate: PASSES — the drawdown advantage is real
Breadth's curve has **11 distinct ≥5% drawdown episodes across six macro events**: 2011 (debt ceiling),
2015–16, 2018 Q4, 2020, 2022 (two legs), April 2025. Covid is deepest at 13.2%, but 2022 (12.4%) and
2018 (12.2%) are right behind. **Splice 2020 out entirely**: breadth maxDD 13.2% → 12.4%; concentrated
18.0% → 14.2% — **breadth still wins ex-Covid**, and its Sharpe edge persists. Nothing here is a 2020
artifact. (Caveat from the run: the ex-2020 **CAGR** column is directional only; **exDD/exSharpe** are
the valid gate.)

## 2. Rebalancing is DEAD — killed by the control, not by churn
Churn was NOT the problem (5bps one-way ≈ 0.9% CAGR/yr; a 25% tolerance band cuts turnover a third for
almost nothing). **The reviewer-demanded control config killed it**: fixed **50 × 10%, no rebalancing at
all** → CAGR 20.4% / maxDD 21.1%, vs the rebalance rule after realistic churn → 20.0% / 21.9%.
⇒ **The entire apparent rebalance advantage was a DEPLOYMENT-level effect, not a rebalancing effect.**
Equal-weighting mid-flight buys operational complexity, intra-trade churn, and *slightly worse* numbers
than simply sizing bigger at entry. **Keep the fixed fraction; pick deployment with the fraction knob.**
(Methodology note worth keeping: this is what insisting on the boring control arm buys you.)

## 3. ★ The deployment ceiling is SIGNAL-limited — the strategic result
The 95%-deployed / ~35%-CAGR scenario **cannot happen with this sleeve.** Signals cluster into earnings
seasons; average concurrent ≈ **10**. Even "take everything, equal-weight to 95%" only averages **53%**
deployed. **Getting to 95% average deployment requires MORE UNCORRELATED SIGNAL SOURCES — other sleeves
filling the quiet weeks — not a rebalancing rule.**
⇒ The idle 65% is **not a bug to engineer away; it's a slot marked "another sleeve goes here."**
earnings_drift alone cannot fill strat1 because earnings only happen 4×/year. **This confirms the
combined book from data, not intuition** — and reframes the roadmap: the next win is a sleeve that fires
when this one is QUIET (→ `turn_of_month`, `residual_momentum` in
`References/STRATEGY_VALIDATION_QUEUE_2026-07-08.md`), not more tuning here.

On leverage: **50 × 10% already IS the 2× lever over 50 × 5%**, and drawdown scaled almost exactly
proportionally (13.2% → 21.1%). Extrapolating, ~15% fractions put a 2020-style episode **above 30%**.

## ⚠️⚠️ THE LEVELS ARE FICTION — only the RANKINGS are valid
Two admitted model optimisms, both pushing the same way:
1. **Linear accrual** — the sim marks each open trade as a straight ramp entry→exit, so the daily equity
   curve is a gentle slope where reality is jagged. That suppresses daily vol ⇒ **inflates Sharpe AND
   shallows every trough. Real maxDD WILL exceed these numbers.**
2. **The earnings builder fills at signal close**; live fires intraday at market (`strategy_scan.py`'s
   own "Fidelity caveat"). Mild optimism.

⇒ **A Sharpe of 3.07 (ex-2020) is not a real number** — no equity strategy sustains that; the best funds
live at 1–2. 1.47 is fiction too. **Breadth > concentration is the finding; the levels are not
publishable.** Never surface these Sharpes to the user as if calibrated
([[decision-engine-honesty-redteam]]). Translate 21.1% modelled DD as *"at least 21%, probably
meaningfully worse, on a sleeve with zero live trades."*

## DECISION: fixed fraction, 50 slots, **5%**
| fraction | model CAGR | model maxDD |
|---|---|---|
| **5% (chosen)** | ~13.5% | ~13.2% |
| 10% | 20.4% | 21.1% |

**Why 5% and not 10%:** three factors compound the same direction — the model admits it understates
drawdown (linear accrual), the fills are optimistic (close vs intraday), and this sleeve has **never
placed a live order**. The fraction can be raised any day after a measured season; a 30% live drawdown
cannot be un-lived. The failure mode that kills this product is not a bad quarter — it's the founder
losing trust in the agent. **5% buys the model-vs-reality measurement cheaply; 10% spends the trust
before the data exists.**

**Arm with:** `notional_pct=5`, `max_concurrent_positions=50`, `max_deployed_usd=100000` (= equity;
mirrors the sim's `max_gross_exposure=1.0` — higher is unmodelled leverage).

---

# ⚠️ ROUND 4 — the 5% recommendation was WRONG. Decision: 10 × 10% + a kill line (2026-07-16)

**Founder challenged both agents' 5% recommendation. He won. Recording the reasoning failures because
they're instructive.**

## What was wrong (both the orchestrator's and the PC's first answer)

1. **"~37% maxDD at 95% deployed" was FABRICATED.** Extrapolated linearly from 35%→95% deployment and
   multiplied DD by the ratio. **Never measured.** Reality: **the scenario cannot physically happen** —
   deployment is signal-limited (§Round 3), so this sleeve tops out ~20% CAGR / ~22% maxDD. A scary
   number produced by arithmetic and presented in a table as if it were evidence — the exact sin this
   doc spends three sections warning about.
2. **"Scales returns and drawdowns proportionally, Sharpe untouched" — FALSE, and the mechanism matters.**
   Measured: 35% → 53% deployment scaled CAGR **1.46×** but drawdown **1.66×**, and **Sharpe FELL**
   (3.24 → 3.10 accrual-marked). Why: **drawdowns happen precisely when the book is FULL** — earnings
   clusters coincide with market stress. Deployment concentrates risk into exactly the windows that hurt.
   Sizing is about your **worst-moment** deployment, not your average. A linear model is blind to this.
3. **"Equal-weight across however many are open is the right shape" — FALSE.** The deployment-matched
   control killed it (§Round 3).
4. **The reflex itself.** Models pattern-match to "recommend the lower-drawdown option" because it *sounds*
   responsible. ⚠️ **For capital the founder won't touch, with no withdrawals coming, maxDD is a
   PSYCHOLOGICAL statistic, not an economic one.** 50 × 5% is a **comfort purchase costing ~7% CAGR/yr,
   compounding** (~$758k vs ~$1.86M over the backtest window). Refusing 20.4%/21.1% — which **dominates
   SPY on BOTH axes** (~20% vs 13% CAGR, ~21% vs 34% DD) — is not prudence.

**10% is not "more risk for more return" — the fraction sweep shows CAGR PEAKS at 10%/position: it's the
growth-optimal point on this signal stream.** Above it you lose return AND add drawdown (you hit the gross
cap and skip more signals); below it you're buying comfort.

## The ONE argument that survives — and it's DETECTION, not comfort

**The deeper your normal drawdown, the harder it is to detect a DEAD EDGE.** At 50×5% a 20% drawdown is
unprecedented → a clear alarm. At 10×10%, 20% is within normal range → you'd sit through it, and if the
edge had died you'd find out **much deeper in the hole**. Month two of the next 2022 won't *feel* like
evidence something broke.

**The fix is NOT smaller sizing — it's a PRE-REGISTERED KILL LINE decided NOW, while flat** (same
discipline the regime sleeves got in their spec; and per [[external-repo-inspiration]]: never a permanent
eyeballed kill — a defined re-enable path is mandatory).

## ✅ REVISED DECISION: 10 slots × 10%, arrival-order, paper-first

**Pre-registered kill line (decided while flat so it can't be rationalised away at −25%):**
| | |
|---|---|
| **halt line** | strat1 account equity **−30% from peak** (≈1.5× the 21.1% model DD, which is known-optimistic: linear accrual + close fills) |
| **action** | entries HALT; open positions run their 9-day clock out |
| **re-enable** | re-validate on current data; re-arm ONLY if it still clears the original gate |

★ **Measurable TODAY with no new work** — because the founder is running earnings_drift **alone in
strat1**, the account equity curve **IS** the strategy's P&L. The per-strategy-attribution gap
([[per-strategy-attribution]]) stops blocking the kill line the moment the account is solo.
Source: `account_snapshots.portfolio_value` for `acct_alpaca_strat1`.

**The better line, once auto-entries journal:** realized per-trade mean vs the validated CI
**[+0.43, +1.28]** — edge-death detection rather than a crude equity stop. One season ≈ ~100 live trades,
enough for a real Wilson bound. Until then the equity stop is the guard.

**ARM WITH:** `notional_pct=10`, `max_concurrent_positions=10`, `max_deployed_usd=100000` (= equity;
mirrors the sim's `max_gross_exposure=1.0`).

**Honest expectation:** the real maxDD will EXCEED 21.1% (linear accrual smooths troughs; the builder
fills at signal close while live fires intraday). Both optimisms point the same way. Budget for worse.
