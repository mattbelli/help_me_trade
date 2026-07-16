# Validating the strategy queue — runbook (2026-07-08)

Three `validate_*.py` stubs for the foundry's candidates
(`References/STRATEGY_VALIDATION_QUEUE_2026-07-08.md`). They wire your **real** harness — the ranking
in `research/momentum_reference.py`, the block-bootstrap CI in `research/significance.py`, and DSR +
PBO in `research/overfit.py` — and print a `strategy_stats.json`-shaped row. **Run on the PC** (Norgate
data lives there). Nothing here is validated until it clears the gate; a row prints `validated:"unverified"`
until it does.

## The gate (every candidate must clear ALL — the same bar dip_buy/earnings_drift cleared)
- **block-bootstrap 95% CI (per-entry/per-month) excludes 0** — `significance_report(...)["block_ci"]`
- **PBO < 0.5** across the param grid — `pbo_cscv(returns_matrix, n_splits=16)`
- **DSR ≥ ~0.95** after deflating for the grid searched — `deflated_sharpe_ratio(returns, n_trials=…, sr_variance=…)`
- **survives ~10 bps/side cost** (`--cost-bps`, default 10)

All three are **canonical published anomalies** → real mechanisms, but potentially decayed/crowded. The
real question each answers is *"still alive post-2010, net of cost?"* — let the CI, not the headline, decide.

## Run order (cheapest / most-diagnostic first)

### 1. `turn_of_month` — do this FIRST (pipeline shakedown)
```
.venv/bin/python tools/validate_turn_of_month.py --bars-db data/research/bars.db
```
Only needs SPY + a calendar — **no survivorship-free data**. Fully self-contained; if this runs clean, your
significance/DSR/PBO plumbing is proven end-to-end before the bigger jobs.

### 2. `residual_momentum` — the real prize (mostly already built)
```
.venv/bin/python tools/validate_residual_momentum.py --bars-db data/research/bars.db
```
**Key: the ranking already exists and is correctness-gated** — `research/momentum_reference.py`
(`compute_target_set`, checked by `tools/momentum_validate.py` against golden fixtures). This stub adds only
the monthly portfolio-return backtest + the gate; it reuses your proven ranking verbatim. Needs the
survivorship-free "Current & Past" Norgate universe in `bars.db` + `research/fixtures/momentum_sectors.json`.
*Simplifications flagged in-file (enter-at-close, delisting-proxy exit) — tighten to your fill model before publishing.*

### 3. `pead_confirmed` — SCAFFOLD, wire 2 seams first
```
.venv/bin/python tools/validate_pead_confirmed.py --bars-db data/research/bars.db
```
The entry gate / exit rules / sim / gate are wired, but two data seams raise `NotImplementedError` until you
connect them:
- **`load_earnings_events()`** → announce dates + surprise. Reuse `research/earnings.py` /
  `earnings_strategy.py` (the earnings_drift source).
- **`load_volume_bars()`** → daily volume (`momentum_reference.load_bars_for_symbols` drops it; `SELECT date, v
  FROM daily_bars`).

## Important caveats
- **Written against your APIs but NOT run here** (no Norgate data on serv) — treat as scaffolding, shake out on
  the PC. `turn_of_month` should run closest to as-is; `residual_momentum` reuses proven code; `pead_confirmed`
  needs the two seams.
- **Set `DSR n_trials` to your TRUE search count.** The stubs pass the in-file grid size; if your research swept
  more parameter cells, DSR must deflate by that real number or it flatters the result.
- **Only publish a `validated:"validated"` row if it PASSES** — and apply the survivorship caveat (the
  earnings_drift lesson) to any earnings-based number.
