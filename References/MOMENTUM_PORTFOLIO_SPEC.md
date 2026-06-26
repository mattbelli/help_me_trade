# Spec for serv — Phase 3: Residual-Momentum Portfolio strategy (the combined book's alpha engine)

*PC→serv handoff. The validated "combined book" = **three strategies sized 70/20/10**, not a monolith:
**residual-momentum portfolio (70% — this spec)**, steep-YC dip (20% — Phase 1, follow-on), VIX-spike (10% —
Phase 2, follow-on). Cash-when-idle is automatic (idle sleeves' capital sits uninvested). This is the **alpha
engine** (+6.4%/yr Jensen alpha, beta 0.76 in backtest) and the hardest build — it needs portfolio-rebalance
framework the per-symbol scan doesn't have. Founder's directive: **ships full-auto + summary alerts, paper-fenced
— a 120-name rebalance is not a phone-approve flow.** serv builds on `main`, independent adversarial review, founder
merges, paper-validate 2-3 monthly cycles before trusting it. Effort est: 5-10 days.*

## Why this doesn't fit today's framework
`signals/strategy_scan.py::_run_strategy()` loops a universe calling `spec.check_entry(ctx)` per symbol →
True/False. Momentum needs to **rank all symbols together** and emit a **target set**, **once a month**, then
**diff against current holdings** and trade the delta. That's a different execution model. Don't bend the
per-symbol path — add a parallel portfolio path.

## The signal (port from research, don't redesign)
Residual momentum, validated in `data/research/_edgehunt/sector_idio_mom_verify.py` / `combined_book_nav.py::run_mom_sleeve()`:
- Monthly, on the last trading day: for each universe symbol compute 12-1 momentum = return over t-252..t-21.
- **Residualize**: subtract the equal-weight mean 12-1 of that symbol's sector. Rank by residual momentum.
- **Target set** = top quintile, **equal-weight**. Hold 1 month. Rebalance = sell drop-outs, buy new entrants,
  hold the rest (no trade).
- Universe = **current S&P 500** (liquid, Alpaca-tradeable; survivorship is moot going forward — you only trade
  what currently exists). Honest note: backtest's +0.30%/mo selection alpha is *fragile* (loses significance
  ex-2020+2026); the book's value is as much **drawdown reduction** as alpha. Credit it honestly in `strategy_stats`.

## New framework pieces

1. **Portfolio strategy dispatch.** Add a strategy "kind" with a `check_portfolio(PortfolioContext) -> list[str]`
   path (returns the target symbol set) instead of per-symbol `check_entry`. `_run_strategy()` detects portfolio-kind
   strategies and routes to a new `_run_portfolio_strategy()` that does rank → diff → execute. Keep the per-symbol
   path untouched for dip/earnings.

2. **Production data pipelines (new):**
   - **Universe daily bars** — momentum needs ≥252 trading days of daily bars for ~500 symbols. Production today
     only has bars for watchlist/traded symbols. Add a daily bar-refresh task (analog of `_earnings_refresh_loop`)
     that maintains daily history for the momentum universe in the production DB; backfill ~13 months on first run.
   - **Sector membership** — `run_mom_sleeve` reads sector from `data/research/fundamentals.db` (research-only).
     Add a production sector table (one-time sync or a fundamentals fetch) covering the universe.

3. **Monthly scheduler + idempotency.** Add `last_rebalanced_month TEXT` to `strategies`. The portfolio path
   checks `current_month == last_rebalanced_month` → skip; only rebalance when the month ticks over. Stamp it
   **only after the rebalance fully completes** (see #8). This prevents re-firing every 300s all month.

4. **Rebalance execution (the auto path).** Compute target set → load this strategy's current holdings from
   `managed_positions` → diff: **sells** = held ∧ ∉ target; **buys** = target ∧ ∉ held; **holds** = both (no trade).
   Each sell/buy goes through the **unchanged chokepoint**: `RiskEngine.validate()` → `ExecutionService.submit()`,
   auto (no proposals). Open a `managed_positions` row per buy (reuse the synthetic-`proposal_id` ledger path from
   the auto-entry feature). Size = `notional_usd / N` per name, whole shares, `max_deployed_usd` hard cap.

5. **Rebalance exit model.** Momentum positions are **rebalance-managed** — they exit *only* by dropping out of
   the target set at the next rebalance, NOT by stop/target/time. **The exit engine must NOT touch portfolio-kind
   positions** (`trading/exit_engine.py::_process_managed_position()` must skip them — mark the managed_positions row
   with the portfolio kind, or a `rebalance_managed` flag). Getting this wrong = the exit engine stop-outs a
   momentum name mid-month and the rebalance re-buys it → churn. Verify the exit engine leaves them alone.

6. **`alert_style` column + summary push.** Add `alert_style TEXT DEFAULT 'per_trade'` to `strategies`. For
   `summary`: the portfolio path collects all rebalance fills, **suppresses the per-trade execution pushes**, and
   sends **one** `send_alert_push`: e.g. *"Momentum rebalance: sold 9, bought 11, holding 60, $X deployed [paper]."*
   The momentum strategy ships `alert_style='summary'`; dip/VIX (few trades) stay `per_trade`.

7. **★ Daily-orders-cap resolution (CRITICAL — would silently truncate a rebalance).** `RiskEngine.validate()`
   enforces `max_daily_orders` (per-account + aggregate). A 120-name rebalance **blows past any normal daily cap and
   half the rebalance gets rejected.** Resolve deliberately: portfolio-kind rebalances must be **exempt from the
   per-order daily-count** (or it's scoped so one rebalance event counts as one, not N). **Keep every other
   RiskEngine check on every order** — per-order value cap, buying power, concentration cap (`max_position_pct` is
   *good* here, it bounds any single name), kill-switch. Only the order-*count* gate needs portfolio-awareness.
   Do NOT just raise the global cap — that weakens the guard for the reactive strategies.

8. **★ Idempotent / resumable rebalance.** A 120-order rebalance can crash partway. Persist the planned diff
   (target set + the buy/sell order list) **before** executing; execute against that plan; on restart, resume from
   where it stopped (skip already-submitted orders — the in-flight/`recent_open_buy_exists` dedup from the auto-entry
   feature helps, extend it to sells). Stamp `last_rebalanced_month` **only on full completion**. A half-rebalance
   that silently re-fires or double-trades is the worst failure mode here.

9. **Bulk-submit pacing.** Don't fire 120 orders in a tight loop — sequence them (small await between submits) to
   avoid hammering the broker / rate limits. Each still individually RiskEngine-gated.

## Safety invariants (unchanged — restate in the PR)
- Every rebalance order still flows kill-switch → `RiskEngine.validate()` → `ExecutionService.submit()`. The
  chokepoint gets **zero changes**. LIVE stays hard-blocked behind the typed double-gate.
- **Paper fence**: this strategy ships `entry_mode='auto'`, honored only in `dry_run`/`paper` (the existing
  3-layer fence). It can never auto-rebalance live.
- Decimal-only for money/qty in `trading/`.
- **Adversarially review** the new portfolio execution path + the daily-cap exemption + the exit-engine skip before
  commit — this submits bulk orders, it's the highest-risk path since the chokepoint itself.

## Operating mode (founder's directive)
Ships **full-auto + summary alerts, paper**. Founder monitors via the summary push + cockpit; does NOT approve
individual names. Seed it **disabled + unbound**; founder binds it to a dedicated paper account and enables it.

## Verify
- Unit: ranking matches the research `run_mom_sleeve` on a fixed date; monthly idempotency (no re-fire within a
  month); diff logic (sells/buys/holds correct); daily-cap exemption lets a 120-order rebalance through while a
  reactive strategy still hits its cap; exit engine skips portfolio positions; summary push fires once; resume
  after simulated mid-rebalance crash doesn't double-trade.
- Paper integration: bind to a paper account, trigger a month-tick rebalance → confirm bulk submit through
  RiskEngine, ~N managed-position rows open, ONE summary push, no per-trade pushes, exit engine leaves them alone;
  then simulate next month → confirm it sells drop-outs + buys entrants + holds the rest.
- Pre-push: `ruff check .` + `pytest -q` + frontend typecheck. Paper-run **2-3 full monthly cycles** and compare to
  the backtest before trusting it.

## After this ships
Add **Phase 1 (steep-YC dip)** and **Phase 2 (VIX-spike)** as their own strategies (auto, sized 20%/10%) to
**complete the book** — they're the tactical sleeves that cut drawdown from ~24% to ~16.5% (the "step away" part).
They need the FRED/regime data pipeline (independent of this momentum build). Running all three at 70/20/10 = the
validated combined book.
