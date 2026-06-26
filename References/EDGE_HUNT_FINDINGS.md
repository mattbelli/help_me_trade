# Edge Hunt — findings (overnight multi-agent run, 2026-06-26)

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
