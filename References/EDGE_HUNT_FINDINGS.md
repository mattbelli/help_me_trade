# Edge Hunt — findings (overnight multi-agent run, 2026-06-26)

*Committed twin of the local research journal `data/research/backtest_lab.md` §9 (gitignored). Keep the two in
sync — this doc is the shareable detail; the journal is the full lab record + idea backlog. This edge hunt's
combined book **supersedes** the journal's §8 dip+earnings combo as the chosen deliverable.*

Multi-round, multi-agent quant edge discovery on the survivorship-free Norgate universe
(`data/research/bars.db`, 910 sym, 2010–2026). Each hypothesis: implemented + backtested by a
research agent, then **adversarially verified** by a second agent (look-ahead / survivorship /
snooping / cost / benchmark audits). Honest gates: block-bootstrap significance *per entry-date*,
OOS hold-out (train ≤2019 / test ≥2020), fair benchmark vs **SPY** (not zero), realistic costs.
Run 1: 36 hypotheses tested, 3 rounds (cut short by a session limit). Scratch code in
`data/research/_edgehunt/` (gitignored).

## ★ Methodological lesson (applies to ALL results)
The verifiers' key catch: a long-only strategy's **raw-return** block-CI captures market **beta**.
The honest test is bootstrapping the **excess return over SPY** (strategy − SPY same window).
Only one confirmed edge cleared *excess-over-SPY* significance independently — the double-filter
momentum. For the rest, the headline numbers overstate alpha; true excess is smaller. OOS strength
is also inflated by the 2020 COVID rebound and the partial-2026 tariff-recovery — strip both as
robustness; most edges survive but shrink.

## Confirmed / candidate edges (best first)

### 1. Sector × Stock double-filter momentum  — STRONGEST (ship-candidate)
Top-3 sectors (3-mo return vs SPY) ∩ top-quintile 12-1 momentum stocks, EW, monthly.
+1.94%/mo vs SPY +1.28% (**excess +0.66%**). Raw block-CI [+1.19, +2.67], p≤0=0.
**The only edge whose excess-over-SPY CI independently excludes zero: [+0.16, +1.18]/mo** → genuine
alpha, not beta. Robust to all 12 parameter perturbations; clean look-ahead. Caveats: monthly
beat-SPY rate is 56% (not the 71% port-positive); OOS strength inflated by partial-2026; ~225
delisted names lack sector data (survivorship gap). `_edgehunt/sector_stock_double_filter.py`

### 2. Cross-sectional momentum 12-1 (Jegadeesh-Titman) — ship-candidate
Top-decile 12-1 momentum, EW, monthly. +2.00%/mo vs SPY +1.22%. Raw CI [+1.18, +2.87], p≤0=0,
14/16 yrs. Robust to block size (3–24) and costs to 50bps; random-decile control = +0.95%/mo true
signal. Caveat: **in-sample excess over SPY only +0.36%/mo** (many pre-2020 yrs near-flat); OOS
boost partly COVID-2020. Honest alpha ≈ **+0.4–0.8%/mo excess**, not +0.78%. `_edgehunt/xsmom_12_1.py`

### 3. Steep-yield-curve-conditioned dip — sharpens our EXISTING dip edge (ship-candidate w/ caveats)
Baseline dip (c>SMA200, −5%/3d, SPY −1.5%/3d) **ties** SPY (+1.12% = benchmark) → no unconditional
alpha. But gating on **yield curve (Y10−Y2 > 0.5)** lifts dip EV to +2.88% vs SPY +1.57%, CI
[+1.31, +3.50], OOS-consistent, threshold-robust 0.2–1.0. Flat-YC (0–0.5) is *destructive* (−3%).
Honest alpha floor ≈ +1.0–1.3%/yr over SPY. Caveats: 4-conditioner screen (some snoop exposure);
OOS leans on COVID + 2025-26 tariff episodes. `_edgehunt/dip_regime.py` → **directly actionable: gate
dip proposals on the yield curve.**

### 4. PEAD — gap-based post-earnings drift (real, small, needs-work)
Buy T+1 open when overnight earnings gap > +3%, hold 20d. +1.74%/trade vs unconditional +1.30% /
SPY +1.23% (**excess ~+0.44%**), 9,809 setups, full-period CI [+0.88, +2.16], 15/17 yrs. Caveats:
OOS **not** significant at the correct block_size=20 (overlapping 20-day holds); earnings.db omits all
257 delisted names (survivorship gap); gap calc slightly impure for AMC announcements.
Complements the in-flight AV-surprise earnings work. `_edgehunt/pead.py`

## Unverified candidates (showed 'edge' in impl; verify cut by session limit — re-check)
- `vol_norm_mom` — volatility-normalized momentum: EV +1.49, CI [+0.89,+2.10], OOS +1.79
- `small_cap_halloween` — small-cap in winter window: EV +2.60, CI [+1.24,+4.24], OOS +3.27
- `sector_idio_mom` — idiosyncratic (residual) momentum: EV +1.75, CI [+1.09,+2.40], OOS +1.96
- `small_cap_mom` (EV +2.24) — impl 'edge' but verifier rejected (likely size+beta, not alpha)

## Tested and REJECTED (valuable negatives — don't re-attack)
- **overnight_drift** — positive but just captures beta/drift; not actionable as framed.
- **low_vol** — ties SPY (no alpha in this large-cap universe).
- **turn_of_month**, **month_end_window** — CI includes 0.
- **gap_fade** (intraday gap reversion) — negative EV.
- **es_orb** (/ES opening-range breakout), **es_overnight** — no edge / not confirmed.
- **xsrev_1w** (1-wk reversal), **high_52w**, **donchian20**, **rsi2_quality**, **size_tilt** — marginal,
  verifiers rejected as fragile or beta.
- **mom_vix_filter**, **breadth_gated_mom**, **hy_spread_mom**, **breadth_thrust_buy**, **pead_vix_calm** —
  conditioners that didn't survive OOS / added nothing.
- **Halloween (standalone)** — seasonal pattern is real (winter EW ≫ summer) but trails SPY ~4%/yr as a
  flat-half-year strategy; useful only as a tilt/overlay, alpha-vs-SPY evaporates if the boundary moves 2wk.

## Bottom line (round 1)
Momentum looked like the real edge family, with the sector×stock double-filter as the star. **Round 2 (below)
revises this** — the honest excess-over-SPY test demoted several round-1 "edges" as beta/crisis-clustered.

---

# Round 2 — the excess-over-SPY gauntlet (REVISES round 1)

Re-tested every candidate bootstrapping the **excess return over SPY** (not raw), with mandatory ex-2020(COVID)
+ ex-2026(tariff) robustness cuts and momentum-crash tail checks. 27 hypotheses, 3 rounds. This is the honest
alpha test — and it changed the standings.

## ★ Confirmed robust edges (survive excess-over-SPY AND the worst robustness cut)
1. **★ Steep-yield-curve dip — STRONGEST, ship-candidate.** Dip entry only when **Y10−Y2 > 0.5**.
   **+1.01%/trade excess over SPY** (CI [+0.30, +1.40], p=0.0014, n=13,102), +0.91% net of cost; OOS 2020+
   **+2.17%**; survives worst cut (ex-2020+2026: +0.74%, p=0.013) across all block sizes 5–60d and 10 seeds;
   survivorship clean; Bonferroni passes. Only caveat: significance fades above YC>0.6 (moderate threshold
   specificity). **Directly actionable: an upgrade to the existing validated dip — gate dip proposals on the
   yield curve.** `_edgehunt/steepyc_dip_prod.py`
2. **Idiosyncratic (residual) momentum — ship-candidate w/ caveat.** Rank by stock 12-1 *minus* its EW-sector
   12-1; long top quintile. **+0.47%/mo excess** (CI [+0.14, +0.83], p=0.003), survives all cuts (worst
   ex-2020+2026: +0.32%, p=0.022), 16/16 yrs gross-positive, mild 2021 tail (−0.77%). Cleaner than plain
   momentum. Caveat: EW vs cap-weight SPY — ~0.1–0.15%/mo may be the size/EW tilt; needs attribution vs an
   equal-weight benchmark. `_edgehunt/sector_idio_mom_verify.py`
3. **VIX-spike reversion — big but rare (pilot sleeve).** Buy the broad market after a VIX spike (>~28),
   hold to reversion. **+3.34%/episode excess** (CI [+1.95, +5.10]), **10/10 episodes beat SPY**, OOS +3.25%,
   survives every jackknife/exclusion. But **n=10 episodes** in 16y (rare) and threshold-sensitive (>28 passes,
   >26 fails). Economically clean ("buy fear"). Deploy small as an opportunistic overlay. `_edgehunt/vix_spike_reversion.py`
4. **VIX + YC two-sleeve** confirms the sleeves are **diversifying** (cross-correlation −0.18) — promising for a
   combined book; needs a clean portfolio-NAV-vs-SPY framing (currently measured as conditional alpha; has a
   cosmetic worst-year bug). `_edgehunt/vix_yc_two_sleeve.py`

## Demoted / killed by the honest test (were round-1 "edges")
- **Sector×stock double-filter momentum** (round-1 #1) — excess CI compresses away when crisis years stripped
  (worstCutSig=FALSE). It was largely **beta + crisis-regime**, not durable alpha.
- **Plain 12-1 cross-sectional momentum** — excess is **regime-clustered**: +0.78%/mo full-sample but ≈0 (even
  slightly negative) in quiet/trending markets (2015–17, 2019, 2021); the alpha lives in 2020 + 2026. Real but
  not all-weather — must be regime-conditioned. The *residual* version (#2) is the better form.
- **PEAD (gap-based)** — does **not** survive on excess basis at the correct block_size=20. Mostly beta.
- **Momentum-crash protection** (SPY>200dma overlay) — **killed the alpha** rather than preserving it.
- **Short-term reversal as diversifier** — dead (+0.01%/wk excess). Not a diversifier here.
- Re-confirmed no-edge on excess: low-vol, overnight drift, turn-of-month, RSI2-quality, breadth thrust,
  /ES overnight, 52w-high, Halloween×YC, rate-spike reversion.

## Net after both rounds
Three genuinely robust, survivorship-free edges emerge — and they're **complementary**:
- **Steep-YC dip** (event-driven mean-reversion; best evidence) — upgrade to the live dip.
- **Residual momentum** (monthly trend core).
- **VIX-spike reversion** (rare opportunistic "buy fear").
VIX and YC sleeves are mildly anti-correlated (−0.18) → a combined book should be smoother than any single one.
**Next: build the combined portfolio NAV vs SPY (Sharpe/drawdown/CAGR — the deployability test the verifiers
keep demanding), finalize the steep-YC dip production spec, and run the residual-momentum EW-benchmark attribution.**

---

# Round 3 — consolidation: the deployable combined book

## ★ THE RESULT: a combined book beats SPY risk-adjusted (confirmed, HIGH confidence)
Static-weight book = **70% residual-momentum core + steep-YC dip (tactical) + VIX-spike (opportunistic)**:

| Metric | Combined book | SPY buy-hold |
|---|---|---|
| Sharpe | **1.42** | 1.03 |
| CAGR | **+18.9%** | +15.2% |
| Max drawdown | **−16.5%** | −23.3% |
| Rolling-12m windows beating SPY | **69.9%** | — |
| Correlation to SPY | 0.87 | — |

**Better return AND lower tail risk.** Survives the worst robustness cut (ex-2020+2026: Sharpe 1.43 vs 1.11) —
not a crisis-year artifact. Refinement: crediting T-bill yield on idle dip/VIX cash lifts it to **Sharpe 1.454,
CAGR +19.4%** (helps most in the high-rate 2022–26 regime).
**Honest caveat: 0.87 SPY-correlated — this is a *better equity book*, not market-neutral alpha.**

## What did NOT improve the book (keep it simple)
vol-targeting (*hurt* — excess went negative), regime-adaptive sleeve weights (no better than static),
a breadth-thrust 4th sleeve (dead), credit-spread timing (dead — sat out the bull market, Sharpe 0.72).
The plain static-weight book is the winner.

## Locked / finalized specs
- **`steepyc_dip` v1.0 (DEPLOY in propose-mode):** entry `c>SMA200 ∧ stock −5%/3d ∧ SPY −1.5%/3d ∧ Y10−Y2>0.5`,
  fill T+1 open; **exit = 10-day fixed hold** (the −3%/+3% sell-half/trail **destroys** the edge → excess −0.12%,
  CI below zero — do NOT use it). +0.88% net/trade excess, CI [+0.18,+1.26], ~20 signal events/yr (~1000 trades).
  ⚠️ Pre-LIVE: entry selects high-beta names that fell hard; much of the excess may be **beta-recovery** — needs
  a **beta-adjusted (Jensen) alpha** check + a portfolio NAV before going past propose-mode.
- **`residual_mom`:** +0.47%/mo vs SPY = +0.18 passive EW/size tilt + **+0.30 genuine selection alpha**; pure
  selection alpha is *fragile* (loses significance ex-2020+2026, p=0.118) but the sleeve's Sharpe (0.998 vs 0.80)
  is robust → good as the **momentum core**, not as a standalone stock-picker.
- **`vix_spike`:** +3.3%/episode, ~0.6 episodes/yr, n=10 pilot; opportunistic "buy fear" overlay, light on capital.

## Bottom line (night's deliverable)
From "the scorer is edgeless" → **a deployable, survivorship-free, 3-sleeve book that beats SPY on both return
and drawdown, robust to crisis-year exclusion**, plus a **locked steep-YC dip spec** that upgrades the existing
live dip. Remaining work is *engineering/validation, not hunting*: (1) beta-adjusted Jensen alpha on the dip +
book (how much is true alpha vs leveraged beta); (2) wire `steepyc_dip` into `strategy_stats.json` as the dip
upgrade; (3) build the book as a live propose-mode strategy. Hypothesis space is well-explored — last round's
refinements all failed, signalling convergence.

---

# Beta-adjusted validation (Jensen alpha — real skill vs leveraged beta)

CAPM/Jensen regressions to separate genuine alpha from market exposure. `_edgehunt/capm_alpha.py`.

## ★ Combined book = REAL ALPHA (confirmed)
Monthly CAPM, 177 months: **Jensen alpha +6.4%/yr** (ex-2020+2026: **+5.0%/yr**), **t-stat +3.6 / +3.0,
p < 0.005 in both windows**. **Beta = 0.76** — the book runs *less* market exposure than SPY, which rules out
"leveraged beta" as the explanation (that would need β>1). R²=0.75. The outperformance is a genuine premium,
coming from the residual-momentum core + the cash-when-idle low-beta design. **This is the trustworthy result.**

## Steep-YC dip = ~half leveraged beta (REVISES the round-3 "strongest edge" framing)
Mean entry **beta 1.30** — the selection rule (stock −5% while SPY −1.5%, above SMA200, steep curve)
structurally fishes high-beta names that overshoot down then bounce harder on the SPY recovery. Decomposition:
- Raw excess +0.98%/trade → **beta-adjusted +0.47%/trade, p=0.078 (NOT significant)**; ex-2020+2026 only
  +0.33%, p=0.27. Beta-stratified: highest-beta names (β 1.5–3.0) show +1.51% raw but **−0.03% beta-adjusted**.
- So the dip's edge is largely *"systematized high-beta dip-buy into a steep-curve recovery,"* not standalone
  alpha. **Revision: keep it as the 20% tactical sleeve (the book as a whole works), but do NOT ship it into
  `strategy_stats.json` as standalone alpha — credit it as high-beta tactical exposure.**

## Net
The deployable result is **the BOOK** (real +5–6%/yr Jensen alpha at beta 0.76), not the dip in isolation.
The residual-momentum core + low-beta-when-idle structure is what creates the genuine alpha; the dip and VIX
sleeves add tactical timing but are beta-flavoured.

---

# Idle-capital policy: cash vs SPY (settled — keep CASH)

What should the ~30% tactical capital do when the dip/VIX sleeves are idle? `_edgehunt/combined_book_idle_spy.py`.

| Config | CAGR | Vol | **Sharpe** | MaxDD | Corr | Beat-SPY% |
|---|---|---|---|---|---|---|
| **① idle = CASH (KEEP)** | +18.9% | 12.9% | **1.42** | **−16.5%** | 0.87 | 69.9% |
| ② idle = SPY | +21.9% | 15.6% | 1.36 | −21.6% | 0.91 | 88.0% |
| ③ control: 70% mom + 30% SPY, no tactical | +19.5% | 16.3% | 1.18 | −23.6% | 0.93 | 79.5% |
| SPY buy-hold | +15.2% | 14.8% | 1.03 | −23.3% | 1.00 | — |

- **Idle-in-SPY HURTS risk-adjusted return**: +3pp CAGR but −0.06 Sharpe, +5pp drawdown, +0.04 correlation — it's
  *pure beta addition*. The book's edge IS its low vol (12.9% vs SPY 14.8%) + moderate correlation; filling dead
  time with SPY directly imports the disadvantages. Gap holds ex-2020+2026 (−0.054 Sharpe), not crisis noise.
- **Tactical timing earns its keep**: ② (1.36) beats the dumb static blend ③ (1.18) by +0.18 Sharpe — so the
  conditional sleeves add real value beyond just SPY exposure, even though the dip is beta-flavoured per-trade.
  (Conditional-deployment value ≠ per-trade alpha — both are true.)
- **Decision: idle stays in CASH (Config ①).** Use idle-in-SPY only if the mandate explicitly wants max CAGR and
  can tolerate deeper drawdowns — it's an "aggressive dial," not an improvement.

## FINAL deployable picture
**The book, idle-in-cash, is the keeper:** Sharpe **1.42** vs SPY 1.03, **+5–6%/yr genuine Jensen alpha** at
**beta 0.76**, max drawdown **−16.5%** vs SPY −23.3%, robust to crisis-year exclusion. It's a *better, lower-risk
equity book* (0.87 SPY-corr — not market-neutral). Sleeves: 70% residual-momentum (the alpha engine) +
20% steep-YC dip (high-beta tactical, not standalone alpha) + 10% VIX-spike (rare opportunistic, n=10 pilot).

---

# Extension — earnings-drift as a 4th sleeve (tested 2026-06-26)

"Next level without overfitting" → add an uncorrelated, **pre-validated** edge rather than refine existing ones.
Earnings-blowout (the §7a strategy / live `earnings_drift`) is calendar-driven → hypothesis: low-correlation to
the price/market-driven sleeves. `_edgehunt/earnings_sleeve_4way.py`.

**Diversification confirmed:** earnings monthly returns correlate **+0.147** with the 3-sleeve book (vs
momentum↔SPY +0.876 for scale; ↔VIX +0.04) — near-orthogonal, a genuine calendar-driven diversifier.

| Book | CAGR | Sharpe | MaxDD | Beta | Jensen α/yr | Beat-SPY% |
|---|---|---|---|---|---|---|
| 3-sleeve (70/20/10) | +18.9% | 1.42 | −16.5% | 0.76 | +6.7% | 69.9% |
| **★ 4-sleeve (60/20/10/10)** | +17.6% | **1.50** | **−14.7%** | 0.66 | +6.9% | 59.6% |
| 4-sleeve (50/20/10/20) | +16.3% | 1.59 | −12.8% | 0.56 | +7.1% | 50.6% |

**Verdict:** earnings genuinely improves the book — but via **risk reduction, not return** (lower vol/DD/beta;
CAGR *drops* since earnings returns less than momentum; alpha preserved, not diluted). 4S-B holds ex-crisis
(Sharpe 1.49 vs baseline 1.43). It's a **non-overfitting** lift (orthogonal pre-validated edge, 3 pre-specified
weights, no grid-search). **The cost:** bull-market beat-rate 70%→60% (lower beta lags in up-years); 4S-C maxes
Sharpe but its beat-rate collapses to ~50% → avoid. **Caveats:** modest (+0.08 Sharpe) and an **upper bound** —
the earnings universe is survivorship-biased (~614 current names), so live would be somewhat smaller (the
*correlation* benefit is robust to that). It's a **risk-preference choice** (4S-B = smoother/lower-DD; 3-sleeve =
more bull participation), not a free win. **Op note:** earnings is event-driven *stocks* → needs its OWN dedicated
account (shared-symbol exit collision, same rule as momentum).

---

# Tier-1 hardening (confidence checks — generalization, PBO/DSR, regime stress)

Run to build trust in the book, not chase return. `_edgehunt/hardening_*.py`. **Verdict: the book holds up.**
*2026-07-16: the NAV/CAPM/PBO/DSR pipeline behind these numbers is now COMMITTED as
`research/book_validation.py` + golden fixture `tests/fixtures/combined_book_monthly_nav.csv` +
CI guard `tests/test_book_validation.py` (board card "Commit the combined-book validation pipeline").
Committed run reproduces: Sharpe 1.418, CAGR +18.9%, maxDD −16.5%, beat 69.9%, PBO 0.0000, Jensen α
+6.43%/yr (vs +6.7% documented — three more weeks of bars, not a logic change).*

- **No survivorship bias.** Residual momentum on the full survivorship-free set (676 syms incl. delisted) ≈ the
  current-500 result (Sharpe 1.19 vs 1.24; excess +6.1% vs +5.9%). The validated numbers are clean. *Caveat:*
  mid-cap **Russell generalization is untested** — bars.db is S&P-only (only 83 non-S&P Russell names); a true
  small-cap test needs a separate Norgate pull.
- **★ NOT overfit (the headline confidence result).** PBO/CSCV = **0.000** (the in-sample-best config beat median
  OOS in every one of 12,870 splits); **DSR survives even at N=76 trials = 0.9997** — the book's Sharpe 1.42 sits
  far above the expected-max-Sharpe-from-fishing (~0.40 even at N=100). The +5–6% alpha is **not** a selection
  artifact across the ~76 hypotheses we tested. Bootstrap Sharpe 95% CI **[0.91, 2.01]**, P(Sharpe ≤ SPY 1.03) =
  6.8% → ~**93% confident it beats SPY risk-adjusted** (the CI low end dips just under SPY's 1.03, so 93% not 95%;
  wide band is just 177 monthly observations).
- **One structural regime dependency — flat/inverted yield curve.** Steep YC (>0.5, 63% of months): Sharpe **2.11**,
  excess +6.7%. Flat/inverted (≤0.5, 37%): Sharpe **0.69**, excess **−0.83%** (lags SPY). The book's primary
  conditional fragility — momentum struggles late-cycle and the YC-dip sleeve is idle then. **The curve has been
  flat/inverted since 2022 and hovers near +0.5 now, so expect compressed alpha in the current regime; the book
  shines when the curve is steep.** All other splits (vol, bull/bear, decade) are positive on both sides; modest
  decade alpha-decay (6.3→3.1→2.2%).

**Net:** well-hardened — not overfit, survivorship-clean, broad regime robustness, with one known YC-regime
dependency to put on the dashboard. Backtesting is exhausted; the honest next step is **forward paper**, not more
analysis. Open data gap: a Norgate mid-cap pull for a true Russell-1000 generalization test.

---

# Active-MR thread #1 — absorption-triggered intraday /ES mean-reversion (cost-killed, but informative)

Founder's tape thesis: volume spike + rejection wick at a volume node = absorption → bounce. Tested on 10y /ES
1-min (3.49M bars), reusing the volume-profile + intraday-reversion machinery. `_edgehunt/es_absorption_mr.py`.

**Cost-killed:** net EV **−0.87 pt/trade** (gross −0.12), block-bootstrap CI [−1.16,−0.54] all-negative,
P(EV≤0)=100%, 870 trades. **63% stop out.** Robust across all 27 param combos (uniformly negative → clean, not
knife-edge). **Key insight:** a 1-min candle cannot distinguish *absorption* (buyers soak sellers → bounce) from
*breakdown* (sellers overwhelm → drop) — they look identical (volume + wick). That distinction lives in **order
flow** (bid/ask volume + delta), which the eye reads on the tape but the candle discards. Gross EV ~0 means a
*faint* signal exists, but it's cost-swamped at the candle level. **This is a data problem, not a parameter
problem** — the real test of the thesis needs Databento order-flow (footprint), which is wired. Don't tweak the
candle version (chasing the least-bad corner = overfitting). Fork: order-flow pull (real test) or shelve.

---

# Round 4 — the banked-scripts sweep (2026-07-02, Fable): five hypotheses, five honest kills

The 6/26 session banked six scripts it never ran (recon confirmed: zero artifacts, zero journal entries).
This round ran them under the full gauntlet — **ledger-first** (`research/HYPOTHESIS_LEDGER.csv`, H77–H81,
registered before execution), excess-over-SPY block bootstrap, worst-cut + snooping + adversarial verify.
Result: **no new sleeve. Five valuable negatives** — and an empirical re-confirmation that the 2010–2026
daily S&P panel's hypothesis space has converged (per the fable deep-dive's cut list: new alpha needs new
data, not new signals on this panel).

## KILLED — pre-earnings drift / "earnings announcement premium" (H77) — definitive
Long D-5 close → D-1 close before announcement, $5/500k filters, −5% stop, 10bps. n=**32,462** (653 syms,
2010–2026). Excess **−0.08%/trade**, CI incl. 0, **5/16** positive years, OOS negative, ex-2020+2026 −0.11%.
The academic "announcement premium" does not exist net-of-SPY on this universe — and earnings.db is
survivorship-biased (653 current names), so truth is *worse*. `_edgehunt/pre_earnings_drift_excess.py`.

## KILLED — PEAD × steep-YC (H78) — the lucky-cell textbook case
CC-ret>+2% on earnings day + YC>0.5, T+1 open, hold 20d. Full-sample looked alive (+0.35%/trade net, CI
[+0.08,+1.09], p=0.011; OOS +0.70% sig) — then the pre-planned probe (`_probe_pead_steep_yc.py`) executed:
**(1) snooping sweep: 1/8 thresholds significant — exactly the chosen 0.5** (0.25 and 0.75 both fail);
**(2) OOS ex-2020: +0.18%, NOT sig** (p=0.084) — the OOS headline was COVID-bounce; **(3) non-monotone
dose-response** (YC 0.5–1.0 sig, YC>1.0 not — steeper should be ≥ as good if causal); (4) worst cut ex-2020+2026
already failed (CI [−0.02,+0.99]). The "YC conditioning" is crisis-year clustering, not a yield-curve mechanism.
Resurrecting killed-PEAD via conditioning = the chase-the-least-bad-corner pattern. Dead.

## KILLED — yield-curve sector rotation (H79)
Steepening→Fin/Mfg, flat→Util/Svc, monthly top-quintile 12-1 mom. Excess **+0.17%/mo** full sample, **OOS
excess NEGATIVE**, ~half of years lose to SPY. ⚠ The banked script's "Significant: True, p=0.0000" was
bootstrapped on **raw net returns** (= beta), not excess — flagged as a script defect; verdict here uses the
excess basis. Positive excess concentrates in steep-YC months (+1.05%/mo, 42 mo) = the already-known regime
effect wearing a worse strategy. `_edgehunt/yield_curve_rotation.py`.

## KILLED — share-buyback momentum (H80) — clean kill of a genuinely new event class
12-mo share reduction >1% (EDGAR shares) + top-80th-pct 12-1 mom, monthly. n=7,877: excess **+0.08%**, CI
[−0.21,+0.37], p(≤0)=0.31. The look-ahead exit filter the probe was built to audit turned out immaterial
(1 stock-month in 16y). Nothing there, honestly established. `_edgehunt/share_buyback_mom_probe.py`.

## NOT CONFIRMED — steep-YC dip graded gate 0.3–0.7 (H81) — keep the single 0.5, adversary was right
Motivation: de-knife-edge the validated dip gate (fable + deep-dive rec) and open it fractionally in today's
flat curve. Result of the threshold sweep looked like a plateau (0.3/0.4/0.5 all ≈+0.91% sig; fades >0.6) —
but adversarial review (Sonnet) refuted the reparameterization: **(1)** at the date level the "ensemble" is
*bit-for-bit identical* to the plain >0.3 gate (YC is a date-level value → pooled per-date means unchanged;
CI [+0.321,+1.436] literally equals the >0.3 CI); **(2)** the marginal (0.3,0.5] band is 43 dates, **37% of
them 2020** — crisis contamination, not structure; **(3)** EV ordering is **non-monotone in YC** (looser
gates score higher — anti-causal, same disease as H78); **(4)** today's spread is **+0.29** so even 0.3 is
closed — the "opens today" motivation was factually false, and picking 0.3 *because* today reads 0.29 would
be threshold-drift snooping. **Verdict: single 0.5 gate stays; ship the honest disclosure "gate closed
(YC=+0.29)" instead** (→ SETUP_AVENUES.md dormancy-visibility). New caveat logged: the validated sleeve's
OOS-only ex-crisis subset (n=47 dates) is not significant on its own, CI [−0.07,+3.36] — thin-sample note,
not a demotion (the full ex-2020+2026 cut still passes: +0.74%, p=0.013). `_edgehunt/steepyc_dip_ensemble.py`.

## Bottom line (round 4 — see round 5 below for the regime-local fork)
Five pre-registered trials (ledger H77–H81, honest n_trials accounting for future DSR), five kills, zero new
sleeves — **which is the converged-panel warning confirming itself in data.** Value shipped: pre-earnings
drift and buyback-momentum permanently retired (don't re-attack); two independent demonstrations that
YC-conditioning of new entries = crisis-clustering artifact (the validated dip survives because it passes
worst-cut + beta decomposition, which these didn't); one script-defect pattern to watch (bootstrapping raw
instead of excess). Next alpha dollars go to **new data**: pre-2010 Norgate (book risk estimate), delisted
earnings backfill, MBO absorption features (setstudy rebuild), Russell universe — per the deep-dive §4.

---

# Round 5 — the REGIME-LOCAL fork (2026-07-02, founder-directed): "what's working NOW"

**⚠ DIFFERENT EVIDENCE STANDARD — separate shelf.** Founder call: stop requiring 16y timeless alpha;
find what's live in the 2021–2026 regime, paper it small, pre-register kill lines, ride until decay
("it has an edge until it doesn't"). These results must NEVER be labeled "validated" in
`strategy_stats.json` — they are **regime sleeves**: paper-first, Tier-B-style honest labels
("regime-local evidence, 5y window, expected finite lifetime"), journaled so calibration accrues.
The regime read they map to: (1) index concentration / leader persistence, (2) conditioned dip-buying →
fast V-recoveries (2020/2022/2024/2025/2026), (3) amplified earnings gaps, (4) episodic vol spikes that
terminate fast, (5) fast sector rotation. Ledger H82–H86. One NEW data point added to the repo:
**VIX 3-month term structure** (`data/research/vix3m_fred.csv`, FRED VXVCLS keyless pull, 2007→).

## ✔ PAPER-QUALIFIED — generals' dip (H82): the high-frequency avenue
Top-30 by 60d dollar volume (point-in-time monthly), c>SMA50, 3d ≤ −3.5% → T+1 open, 10d hold, excess
vs SPY. Full 2021–26: nothing (+0.14%, ns). **But the per-year trend is monotone improving**: 2021 −2.5%
→ 2022 −1.7% → 2023 −0.1% → 2024 +0.4% → 2025 +0.7% → 2026 +6.0%; **last-12m: +3.63%/trade net, CI
[+0.11,+4.66], p=0.021, wr 60%** (h=10d). Honest caveats: last-12m leans on the 2026 recovery leg
(crisis-clustering signature — the exact thing that killed H78/H81 as *timeless* claims); bled in
2022-style rate-shock tape. **Frequency 25.6 setups/mo** in TSLA/NVDA/AMD/PLTR/COIN/AVGO etc. — the LLM
slate avenue win regardless of edge. **Kill line (pre-registered): trailing-6m excess (10d) < 0 at a
month-end check, or trailing-60-trade win rate < 45% → retire.** `_edgehunt/generals_dip.py`.

## ✔ PAPER-QUALIFIED — V-bounce harvester (H83, recovery-exit variant)
SPY ≥5% below 20d high + VIX ≥25 + first higher-low up-day → T+1 open, exit on recovery to the prior
20d high (cap 30td). Post-2020: **8/10 wins, mean +2.9%, median +6.2%**; the three most recent shocks
(Aug-24, Apr-25, Mar-26) each +6–8%. Tail risk: firing early in a protracted crash (Mar-2020 entry:
−9.9% capped; fixed-15d variant would have been −23.9% → recovery-exit is the variant, fixed-hold
rejected). ~1.3 episodes/yr, bunchy. Episodic small-n, descriptive — no bootstrap claimed. **Kill line:
any episode MAE ≤ −15%, or 3 consecutive losing episodes → retire.** `_edgehunt/v_bounce_harvester.py`.

## ✔ PAPER-QUALIFIED — VIX term-structure snap (H86): entry-trigger upgrade, not a new sleeve
NEW DATA: spot-VIX/VIX3M ratio; entry the day backwardation (ratio>1, ≥2d) flips back to contango.
21-day hold post-2020: **13/14 wins, mean +5.0% vs +1.4% unconditional baseline**; ~2.3 episodes/yr.
Near-total overlap with the vix_spike sleeve + H83 → **consolidate: ONE vol-event sleeve, three candidate
triggers (VIX>28-from-calm / drawdown-stabilization / term-flip) paper-traded side by side in the
journal; promote the best trigger after ~5 live episodes.** The term-flip is mechanically the cleanest
(waits for confirmed fear-termination; vol-sellers re-engage). Cluster cap: 1 position per 21d window.
**Kill line: mean 21d return < +2.0% over the next 10 episodes → suspend.** `_edgehunt/vix_term_snap.py`.

## ✘ REJECTED — mega-gap continuation (H84)
Earnings-day gap ≥+6% that holds → T+1, 10/20d. The regime claim is **backwards**: 2021–26 excess
(+0.34%/+0.19%) is WEAKER than 2010–20 (+0.58%/+1.40%), all four cells ns, per-year incoherent. More
gaps in the modern tape (4.1/mo vs 2.3/mo) but no follow-through premium. `_edgehunt/mega_gap_continuation.py`.

## ~ WATCH — fast sector RS (H85)
Top-2 sectors by 1m/3m EW return, monthly. 2021+ positive but ns (1m +0.25%/mo p=0.24; 3m +0.42%/mo
p=0.13); thesis contradicted (3m beats 1m — rotation isn't "fast"); 2024–25 deteriorating; sector
selection does beat the EW-all control. Not paper-ready; recheck after 12 more months. `_edgehunt/fast_sector_rs.py`.

## Bottom line (round 5)
Under the regime-local standard: **2 paper sleeves + 1 trigger upgrade qualified, 1 killed, 1 watch.**
The three qualifiers are two distinct trades: (a) *generals' dip* — high-frequency single-name BTD in the
concentration regime (the LLM-slate avenue), and (b) *the vol-event trade* — one sleeve, three candidate
triggers, now including the new term-structure data. Both ship paper-only with the pre-registered kill
lines above, Tier-B labels, and journal entries so the calibration loop grades them. The regime fork and
the tier-1 book stay on separate shelves — decay here is expected and handled, not a scandal.

---

# Round 6 — earnings-drift portfolio construction: breadth, rebalancing, sizing (2026-07-15, founder-directed)

Not an edge hunt — a **sizing/policy study** on the already-validated earnings-drift setup list (6,624 setups,
614 symbols, 2010–2026, `data/research/earnings_labeled.db`, blowout ≥5%, entry 10td / exit 1td before next
earnings, returns stored NET of 0.20% RT cost). Question: how should the live sleeve deploy capital?
Scripts (committed, repo `research/_edgehunt/`): `breadth_vs_concentration.py`, `covid_and_rebalance.py`,
`_fraction_sweep.py`. Quant-reviewed (verdict believe-with-caveats; both required fixes applied:
start-of-day-equity sizing, ex-2020 CAGR demoted to directional).

**Prerequisite fix — the PC was running stale research code.** The old `portfolio.py` deducted its own 0.30%
default cost on top of net-stored returns (double-count). Merged main (473 commits, the pipeline-honesty
round: cost single-sourcing, next-open fills in `strategy_sim`, CV embargo), rebuilt the dataset, re-ran
everything. All numbers below are post-fix.

**Marking caveat (applies to every number below):** no intra-trade price path exists in the setup list, so sims
mark trades by LINEAR ACCRUAL over the holding window (both policies identically). Sharpes under this marking
are inflated vs event-marking — compare within-table only. Live drawdowns will run deeper than model. The
earnings builder also still fills at signal close (next-open honesty fix hasn't reached `earnings_strategy.py`)
— mild optimism. The 614-name earnings universe carries the known survivorship bias (upper-bound numbers).

## ✔ BREADTH beats concentration (not a Covid artifact)
5 slots × 10% takes 30% of signals (arrival-order clip) vs breadth taking ~all. At matched deployment breadth
dominates every metric. Covid gate passes: breadth's DD is spread over **six distinct macro events** (2011,
2015–16, 2018Q4, 2020, 2022×2, 2025-04; deepest 13.2% Covid, next 12.4% and 12.2%); splice 2020 out and
breadth's maxDD barely moves (13.2→12.4%) while its Sharpe edge persists (3.07 vs 2.06 ex-2020, accrual-marked).

## ✘ KILLED — blowout-magnitude ranking
Decile bucket of `blowout_reaction_pct` (never previously analyzed): NO monotone gradient. Deciles 1–9 bounce
0.2–1.2% mean with decile 7 worst (noise, ~2.6σ across 10 comparisons); only decile 10 (>16% reaction) is
modestly higher (1.33% vs 0.89% pooled, ~1.6σ — not actionable). Don't spend ranking effort on reaction size.

## ✘ KILLED — dynamic equal-weight rebalancing
Founder's proposal (take every signal, equal-weight all open to ~95% target, cap 10%/name, rebalance on every
event) tested with explicit one-way churn on resize deltas. Churn is NOT what kills it (5bps ≈ −0.9% CAGR/yr;
25% tolerance band cuts turnover by a third). The deployment-matched control kills it: **fixed 10%/position
with arrival-order skips beats the rebalance rule after churn** (20.4%/21.1% vs 20.0%/21.9% CAGR/maxDD) with
no mid-trade churn and fewer DD episodes. The apparent rebalance win was purely a deployment-level effect.
Also: "95% deployed" is unattainable — signal flow is earnings-season-clustered, so even take-everything
averages **53% deployed**. Deployment is signal-limited, not policy-limited; more deployment needs a second
uncorrelated sleeve, not a sizing rule.

## ★ CHOSEN POLICY — fixed 10 × 10%, arrival-order (the fraction sweep's interior optimum)
Sweep of per-trade fraction at 50 slots: CAGR **peaks at 10%** (20.4%/21.1% maxDD) — 15%+ fractions LOSE CAGR
while DD balloons (bigger positions fill the book faster → skip more signals → breadth, the actual edge,
erodes). Note 10×10% ≡ 50×10%: at 10%/position the no-leverage constraint caps the book at 10 open names, so
the slot cap never binds. Two-point menu: **50×5% = 13.7% CAGR / 13.2% maxDD** (comfort) vs
**10×10% = 20.4% / 21.1%** (growth-optimal). Founder chose 10×10% (drawdown-tolerant, no withdrawals).
Year-by-year: one losing year in 16 (2022: −3.1% vs SPY −18.2%); loses to SPY in ~1/3 of years (strong bulls,
book only ~48% deployed); **2020 (+73.5%) does heavy CAGR lifting** — ex-2020 typical year ≈ 15–18%.
**Pre-registered kill line required before live:** entries halt + re-validate if sleeve DD exceeds ~1.5× worst
backtest episode (≈32% model-marked) or trailing-2y return goes negative while SPY is flat. Paper first.

## Bottom line (round 6)
Sizing policy settled: **fixed 10% of equity per position, max 10 concurrent, skip when full** — simplest rule
won every control. Ranking-by-reaction and dynamic rebalancing are both honest kills. The next CAGR unlock is
a second uncorrelated signal source to fill the ~50% idle time, not leverage or cleverer sizing on this sleeve.

## ✔ CONFIRMED (round 6 addendum, 2026-07-15) — the AMC extra-day exit PAYS
Serv-briefed experiment (`References/EARNINGS_DRIFT_EXTRA_DAY_EXPERIMENT.md` — full results appended there):
hold earnings_drift through the report day and sell at its close when the session is AMC (print lands after
the close). 4 pre-registered arms + negative control on EDGAR `acceptanceDateTime`-derived sessions (53,315
events refetched with the raw stamp; live rule `session_hour` imported, not reimplemented; arm A reproduced
the stored 6,624 baseline exactly). **Δ +0.227%/trade conditional on AMC (CI [+0.058, +0.402], p=0.005),
+0.11%/trade portfolio-wide; survives the conservative day-D-OPEN fill (p<0.0001)**; negative control behaved
(hold-through-everything loses significance, gap-day variance visible: bmo extra-day stdev 7.15 vs amc 3.19).
Measured on biased-conservative EDGAR labels ⇒ a FLOOR; live gates on Finnhub's forward hour, fail-closed on
bmo/dmh/null. **Directly shippable: ~+12% EV lift on the sleeve for zero extra trades.**

---

# Round 7 — universe expansion: Russell Mid Cap C&P + two methodology audits (2026-07-16, founder-directed)

Infrastructure first: Norgate Platinum confirmed **still active**; `bars.db` expanded 909 → **2,204 Norgate
symbols** (6.1M bars; Russell Mid Cap Current & Past added — 2,024 new names, 984 pre-2010 delistings skipped;
bars end 2026-06-18 pending a manual NDU update click). EDGAR earnings refetched for every cached symbol.
Expanded strategy build (same rules, **0.30% RT cost** for mid-cap spreads) → `earnings_labeled_midcap.db`:
13,046 setups / 1,238 symbols; canonical 6,624 S&P dataset untouched. Universe membership snapshotted to
`data/research/universe_snapshot.json` (2026-07-16). Scripts (committed): `survivorship_wedge.py`,
`ordering_fragility.py`, `midcap_expansion.py`. Alpaca symbol audit: 99.0–99.8% of S&P 500 / Russell Mid Cap /
Russell 2000 members tradable — execution is not a constraint at any cap tier we'd use.

## ⚠ METHODOLOGY — same-day tie order can inject look-ahead into `backtest_portfolio`
`backtest_portfolio` date-sorts **stably**, so same-day signals compete for scarce slots in caller list order.
Sorting the trade list by anything touching the return column silently rigs crowded days: on the 6,624-trade
baseline, return-ascending ties give **−3.3% CAGR**, return-descending **+45.4%** — vs a tight fair envelope of
**18.96% ± 0.83pts (sd)** across 10 return-blind shuffles (DB order sits at the mean, so Rounds 6/6a stand).
Rules going forward: (1) never deterministically sort trade lists fed to the sim by any outcome-correlated key;
(2) report portfolio numbers as the mean over ≥10 shuffled orderings. Flip side worth remembering: the fair-vs-
lookahead gap (19% → 45%) says **crowded-day signal selection is a huge open lever** if any live-knowable
ranker exists (blowout magnitude isn't it — Round 6 kill).

## ✔ MEASURED — the survivorship wedge on this strategy (and why it's not a simple haircut)
Same sim, trades filtered to today's S&P 500 members (= what free survivor-biased data would show): per-trade
quality is *overstated* (0.95% vs 0.89% honest) but portfolio CAGR is *understated* (16.5% vs 19.0%) — dropping
the 27% of trades in later-departed names starves the 10-slot book, and idle slots cost more than the quality
bump pays. Smoothness is overstated both ways (maxDD 17.1% vs 22.2%, Sharpe 1.42 vs 1.34). So a scalar
"survivorship haircut" corrects the wrong direction on CAGR — bias is strategy- and metric-dependent; the
founder's post-lapse plan is cached history (never expires) + forward live collection (unbiased by
construction), not statistical add-back. Silver lining: departed-only trades still made +0.73%/trade / 54.9%
win — the blowout condition partially shelters the strategy from delisting-bound names, and the edge is not
"buy future index winners."

## ✘ KILLED (as a book addition) — mid-cap expansion at current settings
10 × 10%, fair-shuffle means [min..max over 10 seeds]:

| config | n | CAGR | maxDD | Sharpe | invested | per-trade |
|---|---|---|---|---|---|---|
| A. S&P 500 C&P only (0.20%) | 6,624 | **18.96%** [17.3..20.0] | 23.5% | 1.33 | 47.8% | 0.890% |
| B. mid-cap-only new names (0.30%) | 6,521 | 13.72% [11.7..15.8] | 31.4% | 0.91 | 41.4% | 0.457% |
| C. combined | 13,145 | 16.88% [15.3..19.3] | 41.8% | 0.96 | 59.3% | 0.675% |
| D. combined, mid legs 0.40% | 13,145 | 15.62% | 42.5% | 0.90 | 59.3% | 0.625% |
| E. combined, S&P-priority tiering | 13,145 | 16.60% | 40.6% | 0.96 | 59.3% | — |

The blowout edge **exists** in mid caps (B positive at honest costs) but at *half* the per-trade strength
(0.46% vs 0.89%), roughly matching SPY standalone with deeper DD. Merging fills more capital (48% → 59%
invested) yet **loses ~2 CAGR points and nearly doubles maxDD**: weak mid-cap signals steal multi-day slots
from stronger S&P ones. Day-level S&P-priority tiering (E, return-blind, live-implementable) doesn't fix it —
slots are held ~13 days, so priority at entry can't protect against a mid-cap taken Monday blocking a better
S&P signal Wednesday. **Keep the sleeve S&P-only.** Revisit only as a *capacity* answer (if the account ever
outgrows S&P signal flow) or with a mid-cap-specific quality gate — not as a CAGR play.

**Benchmark context (total-return B&H over the same window):** SPY 14.92%/−33.7%, IWR (Russell Mid Cap)
12.59%/−40.6%, MDY 12.20%, IWM 11.78%. So the mid-cap sleeve **beats its own asset class** (+1.1pts over IWR,
maxDD −31% vs −41%, at only ~41% invested) — the mechanism transfers, now confirmed in a second universe; what
kills it is the class itself lagging SPY by 2.3pts/yr this window. The kill is an *allocation* verdict against
the SPY opportunity cost, not an edge falsification. If mid caps ever lead a cycle, the validated sleeve is on
the shelf.

## Bottom line (round 7)
Universe expansion was worth building (data + wedge + audits now permanent assets) and honest to kill as a
book upgrade: **more signals ≠ better book when the marginal signal is weaker and slots are shared.** Round 6's
"second *uncorrelated* sleeve" conclusion survives — same-strategy-more-names is correlated and dilutive. The
two audits (tie-order look-ahead, survivorship wedge) harden every future portfolio number we publish.
