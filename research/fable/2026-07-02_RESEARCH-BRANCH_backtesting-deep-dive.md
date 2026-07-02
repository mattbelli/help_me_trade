# RESEARCH BRANCH deep dive — backtesting & strategy development audit

*Branch: `research/decision-engine-data-layer` · Date: 2026-07-02 · Follow-up to the repo-wide
`research/fable/` audits, focused specifically on the research pipeline (`research/*.py`,
`data/research/_edgehunt/`), the strategy findings, and the adaamset setstudy.
Produced by orchestrator + 4 parallel Sonnet deep-reads (fable docs, pipeline code,
findings inventory, setstudy/MBO).*

---

**TLDR:** The research *discipline* here is genuinely strong — excess-over-SPY testing,
killing your own round-1 winners, PBO/DSR, block bootstrap, committed negative results.
That's better than most professional retail quant work. But the deep code audit found
**two active biases inflating every metric in the ML track** (full-sample z-score leakage,
same-bar-close fills) and found that the multiple-testing protections **exist in code but
aren't actually wired in** (DSR's `n_trials` is never tracked, PBO is never called from the
pipeline). On the key questions: regime-*stratified* reporting yes, shorter regime *windows*
no; exits should stay dumb (our own data proves this); the biggest untapped data is pre-2010
history, delisted-name earnings, and the 7GB of MBO parquets that setstudy never actually reads.

---

## 1. What the audit found — the good and the broken

**Genuinely good** (confirmed by both the fable docs and fresh code reading): survivorship-free
Norgate universe, purged K-fold with embargo, overlap-aware block bootstrap, conservative
same-bar stop∧target = loss, the beta/Jensen decomposition that demoted the "strongest edge"
to tactical beta, and the excess-over-SPY revision that killed most round-1 winners. The
combined book (residual-mom 70 / steep-YC dip 20 / VIX-spike 10, Sharpe 1.42, Jensen α
+6.4%/yr, PBO=0.000) survived a real gauntlet.

**Broken — found in this pass, not in the fable docs:**

1. **Full-sample z-score leakage** — `research/dataset_io.py:119-121` computes means/stds over
   ALL rows (including future test-fold samples) *before* handing data to purged K-fold. Same
   for median imputation at lines 112-117. Every OOF metric in the model/calibration track
   (`train.py` → `model.json`/`ev_table.json` artifacts) is inflated. The most corrosive kind
   of leak because it's invisible in results. **Scoping note:** the tier-1 book numbers come
   from the rule-based `_edgehunt` track, which doesn't use this path — the book survives, but
   the calibrated win%/EV artifacts the app consumes do not get a clean bill.
2. **Same-bar-close fills** — `labeling.py:31` and `strategy_sim.py:50` fill at the signal
   bar's close. Real execution is next-bar open, which for mean-reversion entries is
   systematically worse (~0.2–0.5%/trade optimism). This one *does* touch the rule-based
   strategies.
3. **DSR/PBO are decorative as wired** — `overfit.py:63` requires the caller to pass
   `n_trials`; nothing tracks it. `pbo_cscv` is never called from `train.py` or `combined.py`.
   The EDGE_HUNT DSR N=76 was a manual, honest count — but the fable doc already flagged it
   undercounts pre-hunt sweeps and grids, and there's no committed hypothesis ledger to make
   the count auditable.
4. **Cost accounting is inconsistent** — `strategy_dataset.py:601` defaults `cost_pct=0.0`,
   `portfolio.py:139` defaults `0.30`, `combined.py:115` passes `0.0` expecting costs already
   baked in. Nothing enforces the boundary; double-counting or zero-cost reporting is possible
   depending on call path.
5. **Smaller mechanics:** no embargo at the TRAIN/TEST boundary (`combined.py:17-18` —
   late-Dec-2020 entries have label windows bleeding into the 2021 test period); gap-through
   stops fill at the stop price (`strategy_sim.py:93-112`) rather than the open — matters most
   for earnings-adjacent trades; Platt is fit and evaluated on the same OOF pairs
   (`train.py:53-55`).

And the single most important process finding, straight from the fable audit: **the headline
book numbers are not reproducible.** Everything lives in gitignored `data/research/_edgehunt/`
scratch (90+ scripts). A sign error in the CAPM regression would never be caught. Porting the
combined-book NAV backtest + PBO/DSR into `research/` with a golden NAV fixture is the fable
doc's #1 move — agreed, and it's also the prerequisite for fixing items 1–5 with confidence,
because you can't verify a fix changed nothing it shouldn't without a fixture.

## 2. Should we backtest on shorter regime-based windows?

**No to shorter windows, yes to regime conditioning — and the distinction matters.** Shorter
windows shrink the sample, and the edges are already sample-starved (VIX-spike: n=10 episodes
in 16 years). Fitting or selecting on regime sub-windows is how you manufacture overfit — it
multiplies the hypothesis family exactly where DSR isn't tracking it.

What actually helps, in order of value:

- **Extend the sample backwards, don't slice it thinner.** Norgate has pre-2010. The book is
  70% momentum and has never seen the 2009 momentum crash, 2008, or 2000–02. This is the one
  cheap test that could materially change the book's risk estimate — MaxDD −16.5% is
  conditioned on a friendly regime class.
- **Regime-stratified *reporting* on the full sample.** The killer stat is already known
  (book Sharpe 0.69 when YC ≤ 0.5 — which is now). Make VIX-tercile and YC-bin P&L splits a
  standard output of every backtest in `portfolio.py`/`combined.py`; `regime.db` already has
  the data and is currently unused for conditioning.
- **Pre-registered regime-conditional benchmarks for paper validation.** Without them, paper
  trading in today's flat-YC regime will honestly undershoot the backtest average and you
  won't be able to tell "broken" from "dormant." This doc needs to exist *before* the first
  monthly rebalance completes.
- **Parameter ensembles instead of regime-tuned thresholds.** The YC>0.5 and VIX>28 gates are
  knife-edges (YC fades at 0.6, VIX fails at 26) sitting exactly where 2026 data is flapping
  them. Blending YC 0.3–0.7 and VIX 26–30 is robustness for free.

## 3. Should we focus on different entry/exit strategies?

**Mostly no — our own data argues for dumb exits.** The sell-half/trail variant *destroyed*
the steep-YC edge (excess went negative); fixed 10-day hold is the only exit that works.
Vol-targeting the book hurt. Regime-adaptive weights added nothing. This is a consistent
pattern: for daily-bar swing edges, exit engineering subtracts. The alpha is in the *entry
condition and the regime gate*, not the exit.

Where entry/exit work IS worth budget:

- **Fix the fills first** (next-open entry, gap-through stops) — this isn't a new strategy,
  it's making every existing number honest. Do this before any new entry/exit research or
  you're comparing candidates on a biased simulator.
- **The banked conditioned variants** in `_edgehunt` that follow the one pattern that has
  actually worked (macro-gating a beta-ish entry): `pead_steep_yc.py`,
  `steep_yc_sma50_dip.py`, `dispersion_conditioned_mom.py` sit untested. Each one goes in the
  hypothesis ledger and counts against DSR.
- **Stop hunting the same panel for new entries.** The fable doc's cut list says it plainly:
  the 2010–2026 daily S&P hypothesis space has converged. New entry ideas on the same data
  are now mostly multiple-testing risk. New alpha requires new data (below), not new signals
  on old data.

## 4. What data are we not looking at?

Ranked by edge-per-effort, merging the roadmap's table with what the audit found on disk:

| Data | Why | Cost |
|---|---|---|
| **Pre-2010 Norgate daily** | Momentum crash, 2008; changes book risk estimate | $0, already subscribed |
| **Delisted-name earnings** | `earnings.db` omits all 257 delisted names — earnings_drift's "validated" label and the 4th-sleeve numbers are survivorship-biased upper bounds | Backfill effort |
| **The MBO parquets we already own** | 96 files, ~7GB, pulled for $37 — and neither setstudy nor adam_trigger_study reads a single one (§5) | $0, on disk |
| **Russell/mid-cap universe** | Generalization test for residual-mom; small_cap_halloween is unverifiable on an S&P-only panel | One Norgate pull |
| **Forward earnings-calendar vintage** | Backtest used historical next-earnings dates; live uses a forward calendar — mismatch untested, and earnings-drift is the designated live canary | Audit effort |
| **Breadth/sector RS as features** | On disk in `regime.db`/momentum bars, never conditioned on | $0 |
| **Our own live data** | Decision journal + paper fills vs shadow-sim twins — the only data that validates implementation fidelity | Free, compounding |

Correctly deprioritized (agree with the roadmap): options flow at retail, analyst ratings,
social sentiment except inverted as a risk flag. The $0/mo-until-proven-lift rule is right;
QuiverQuant stays unpurchased.

## 5. Improving the adaamset setstudy

The audit's most actionable finding: **the study bought order-flow data and then didn't use
it.** Both harnesses (`research/setstudy.py`, `research/adam_trigger_study.py`) run on 1-min
OHLCV only — the exact resolution the absorption-MR kill already proved can't see absorption.
Meanwhile `research/_edgehunt/mbo_engine.py` already implements everything needed
(`extract_trades` line 215, `reconstruct_book` line 255, `compute_absorption_read` line 406)
and only uses it to render PNGs.

Second problem: **the trigger set is contaminated.** Of 137 triggers, only **19 are
forward=True and directional** — the rest are hindsight recaps and position updates that the
regex classifier (`es_trigger_extract.py:168`) can't reliably separate from live calls. The
headline "44% hit rate, +2.18 pts avg" is descriptive noise over a selection-biased sample.
And the 20-pt "direction worked" threshold is nearly always hit within 24h on an instrument
with 40–80pt daily range.

The fix sequence:

1. **Filter to forward+directional (N=19) before any stats.** Accept the tiny N honestly.
2. **Extract MBO features for all 96 parquets** — ~100 lines wiring `extract_trades` +
   `reconstruct_book` into `{net_delta_at_level, resting_bid_at_level, absorption_flag}` in a
   ±3-bar window around each call, joined on post_id.
3. **Test the actual hypothesis:** Adam's call + confirmed absorption → higher hit rate than
   Adam's call alone. This is the question the MBO data was bought to answer.
4. **R-multiples instead of the 20-pt binary**, and **bootstrap CIs via `significance.py`**
   so a 15–25-trade conditioned sample reports honest error bars.
5. **Grow N prospectively.** This study's real design is forward-looking: log calls in real
   time (forward-only by construction — kills the selection bias), pull the MBO window after
   ($37/97 windows is cheap). Deeper reframe: *Adam is the teacher, absorption is the
   hypothesis.* Once the absorption flag is a numeric feature, we can scan for absorption at
   volume nodes independent of his tweets — that's the absorption-scanner idea in
   ORDERFLOW_GUI_SPEC, and it's the only version with enough sample to ever validate.

## 6. What to bring into the main app

Two urgent correctness items first — both corrupt the decision loop *today*:

- **H6 — fabricated EV on phone proposals** (`signals/decision_scorer.py:76-87`): an
  out-of-range win_prob inherits the *highest* bucket's EV. Five-line fix (return `None`).
  This is the exact fabricated-number failure the mission forbids, on the phone-approval path.
- **C1 — paper+real blended in the agent's performance context**: sandbox fills are being
  summed into "your track record" and seeded into retrospective lesson-memories. The agent's
  self-calibration loop is learning from fiction. Thread `real_only` through.

Then the wiring tier, where research value is already computed but invisible to the agent:

- **Regime feed** (trivial wrap — the book's own conditioner; the agent can't say what regime
  it's in)
- **Screener/candidate generation** (the agent has no top-of-funnel at all)
- **Calibration verdicts** ("your 60-80% bucket realizes 45%" injected into every slate — the
  headline trust metric)
- **`strategy_stats.json` honesty labels** (dip_buy ≈ ties SPY unconditionally;
  earnings_drift survivorship caveat)
- **FRED production pipeline** — without it steepyc_dip and vix_spike can never fire on serv

The bigger builds (momentum portfolio production path per MOMENTUM_PORTFOLIO_SPEC) come
after. The orderflow GUI stays research-side — it's a study instrument, not agent
infrastructure.

## 7. The ongoing research cadence

- **Commit the book** (fable's #1, and this audit's #1): port the combined-book NAV backtest
  + CAPM + PBO/DSR from `_edgehunt` scratch into `research/` with a golden monthly-NAV
  fixture in CI. Everything else builds on having a reproducible baseline.
- **Keep a committed hypothesis ledger** — a CSV of every strategy/variant tested, appended
  per hunt, feeding `n_trials` into DSR automatically. Turns the best habit here (honest
  trial counting) from memory into infrastructure.
- **Pre-register before data arrives**: the paper-validation protocol (bootstrap cone,
  regime-conditional benchmarks, kill/promote lines) locked before the first rebalance;
  shadow-sim tolerance bands before the first momentum cycle. Validate at book level, not
  per-sleeve.
- **Earnings-drift as the canary** (~400 setups/yr, only strategy that reaches live
  significance in ~a year) — but fix the survivorship gap and calendar-vintage mismatch first
  or the canary is miscalibrated.
- **Weekly**: the /ES 1-min archive pull (already happening), plus prospective Adam-call
  logging. **Quarterly**: offline retrain, with journaled-win_prob Brier trending down QoQ as
  the single headline metric of whether the whole loop is working.
- **Graduate to small real money sooner than instinct says** (via the Alpaca double-gate) —
  for liquid names, paper fills teach little beyond the shadow sim; tiny live size teaches
  fill quality and behavior.

---

## Suggested execution order

1. **H6 + C1 fixes** — small, mission-critical, app-side.
2. **Pipeline honesty fixes** — z-score leakage (`dataset_io.py`), next-open fills
   (`labeling.py`/`strategy_sim.py`), cost-boundary enforcement, TRAIN/TEST embargo — then
   regenerate artifacts and see which numbers move.
3. **Commit the book** with golden NAV fixture.
4. **Setstudy MBO rebuild** (§5 sequence).
