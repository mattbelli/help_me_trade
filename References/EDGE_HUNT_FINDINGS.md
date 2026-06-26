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

## Bottom line
**Momentum is the real, robust edge family here** — and the *sector×stock double-filter* is the one that
proved genuine alpha over SPY. The *steep-YC dip* is an immediately usable refinement of our existing
validated dip. Everything else is either beta in disguise or noise. Next: re-test all momentum variants on
an **excess-over-SPY** basis, add momentum-crash (2022) protection, and combine the winners.
