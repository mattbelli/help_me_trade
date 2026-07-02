---
name: edge-hunt
description: Test a strategy hypothesis with the full validation gauntlet (ledger → honest sim → excess-over-SPY → cuts → adversarial verify → findings doc). Use whenever backtesting a new strategy idea, a variant of an existing sleeve, or re-validating a claim. Prevents the known failure modes: multiple-testing inflation, fill optimism, knife-edge thresholds, beta sold as alpha.
---

# Edge hunt — the validation gauntlet

This encodes the discipline that produced the tier-1 book (and killed ~30 plausible-looking
strategies honestly). Every step exists because skipping it once produced a wrong number.
Full history + verdicts: `References/EDGE_HUNT_FINDINGS.md`. Pipeline audit + known biases:
`research/fable/2026-07-02_RESEARCH-BRANCH_backtesting-deep-dive.md`.

## 0. Before running anything

- **Ledger first.** Append a row to `research/HYPOTHESIS_LEDGER.csv` (create with header
  `date,hypothesis,variants_planned,data,verdict,notes` if absent) BEFORE the first run.
  Parameter-grid cells count as variants. This ledger is the `n_trials` fed to
  `deflated_sharpe_ratio` — an untracked trial makes DSR decorative.
- **Check the cut list.** The 2010–2026 daily S&P panel's hypothesis space has converged —
  new signals on that same panel are mostly multiple-testing risk. A new hunt should bring
  new data (pre-2010, new universe, intraday/MBO, new event source) or a macro-gate variant
  of a validated pattern. If it's neither, say so to the user before burning a trial.
- **Data**: Norgate `bars.db` only for equities (survivorship-free, 910 syms). Any other bar
  source gets an explicit survivorship caveat in the verdict.

## 1. Simulation honesty (non-negotiable settings)

- Entry at **next-bar open** after the signal bar — never the signal bar's close.
- Stops fill at the **open when price gaps through**, not at the stop price.
- Same-bar stop∧target ambiguity resolves to the **loss**.
- **Costs stated once**, explicitly, at the trade level (`cost_pct` — don't rely on module
  defaults; `strategy_dataset`/`portfolio`/`combined` have inconsistent ones). Shorts include
  borrow. Report gross AND net; verdicts use net.
- No normalization/imputation fit on the full sample if a model is involved
  (`dataset_io.py` had this bug — fit stats on train folds only).

## 2. Measurement basis

- **Excess over SPY**, aggregated to entry-date level, block bootstrap via
  `research/significance.py`. Raw return CIs and IID CIs are never the headline.
- Minimum viable evidence: net-of-cost excess CI excluding 0. Thin-n (<~100 trades) results
  are "descriptive" by definition — label them so.

## 3. Robustness cuts (all of them, every time)

- **Ex-crisis**: strip 2020 + 2026 (the flattering rebounds). Must survive.
- **Regime-stratified**: YC bins (>0.5 vs ≤0.5) and VIX terciles. A strategy that only works
  in one regime is a *gated* strategy — say which gate, don't average over it.
- **Parameter plateau**: test each threshold's neighbors. If >28 passes and >26 fails, that's
  a knife-edge — either blend the ensemble (26–30) or kill it. Never ship the lucky cell.
- **Beta/Jensen decomposition**: regress excess on SPY; report alpha t-stat. Beta-recovery is
  a legitimate tactical sleeve but must never be labeled standalone alpha (the steep-YC
  lesson).

## 4. Verdict

- Run the result past the **quant-reviewer** agent (adversarial pass) before promoting.
- Verdicts: **validated** (survives everything incl. DSR vs the ledger count) / **promising —
  thin sample** (CI clean but n small; pilot-size only) / **killed** (say why in one line).
- **Write the verdict into `References/EDGE_HUNT_FINDINGS.md` either way** — kills are as
  valuable as wins here. Update the ledger row. If the strategy would ship, its
  `strategy_stats.json` entry must carry honest caveats (survivorship, beta framing,
  regime dependence) — label drift there reaches the phone UI.

## 5. What NOT to do

- No exit engineering on daily-bar swing edges — fixed-hold/rebalance exits beat managed
  exits in every test here (sell-half/trail destroyed the steep-YC edge). Keep exits dumb
  unless the hypothesis IS the exit.
- No resurrecting the four-way-killed (profile-MR, absorption-on-candles, short mirror,
  plain 12-1 momentum) without new data.
- No headline number that exists only in gitignored scratch — port it to `research/` with a
  fixture if it's going to be believed.
